function app() {
  return {
    // State
    tasks: [],
    activeRuns: [],
    archive: [],
    sources: [],
    activeSourceId: null,
    addSourceLoading: false,
    confirmRemoveSourceId: null,
    confirmRemoveSourceName: '',
    launchOpen: false,
    selectedTaskId: null,
    selectedSourceId: null,
    selectedSpec: null,
    specLoading: false,
    sortColumn: null,
    sortDirection: null, // null | 'asc' | 'desc'
    expandedRuns: {},
    expandedArchive: {},
    runLogs: {},
    renderedLogHtml: {},
    _logRenderTimers: {},
    ws: null,
    wsConnected: false,
    confirmStop: null,
    selectedArtifact: null,
    artifactLoading: false,
    specEditInstruction: '',
    specEditLoading: false,
    specEditError: null,
    specEditedContent: null,
    specOriginalContent: null,
    queuedTasks: [],
    currentBranch: null,
    checkoutLoading: false,
    archiveFilter: 'all',
    _pollInterval: null,
    _timerInterval: null,
    // Groups
    groups: [],
    groupSelectionMode: false,
    selectedForGroup: [],
    groupCreateOpen: false,
    newGroupName: '',
    expandedGroups: {},
    // Delete spec
    confirmDeleteSpec: null,
    deleteSpecLoading: false,
    // Rate limit waiting
    rateLimitWaiting: [],
    // Schedules
    schedules: [],
    scheduleModalOpen: false,
    scheduleTarget: null,
    scheduleMode: 'delay',
    scheduleDelayMinutes: 30,
    scheduleFixedTime: '',

    async init() {
      await Promise.all([
        this.fetchTasks(),
        this.fetchActiveRuns(),
        this.fetchArchive(),
        this.fetchGitStatus(),
        this.fetchSources(),
        this.fetchQueue(),
        this.fetchGroups(),
        this.fetchSchedules(),
        this.fetchRateLimitWaiting(),
      ]);
      this.connectWS();
      // Poll active runs + git status + queue every 5s
      this._pollInterval = setInterval(() => {
        this.fetchActiveRuns();
        this.fetchGitStatus();
        this.fetchQueue();
        this.fetchGroups();
        this.fetchSchedules();
        this.fetchRateLimitWaiting();
      }, 5000);
      // Poll logs every 3s as fallback for WebSocket
      this._logPollInterval = setInterval(() => {
        for (const run of this.activeRuns) {
          if (this.expandedRuns[run.taskId]) this.loadLog(run.taskId);
        }
      }, 3000);
      // Update elapsed timers every 1s
      this._timerInterval = setInterval(() => {
        this.activeRuns = this.activeRuns.map(r => ({
          ...r,
          elapsedSeconds: r.elapsedSeconds + 1,
        }));
        // Update schedule countdowns
        for (const sch of this.schedules) {
          if (sch.status === 'pending' && sch.remainingSeconds > 0) {
            sch.remainingSeconds = Math.max(0, sch.remainingSeconds - 1);
          }
        }
        // Update rate limit countdowns
        for (const rl of this.rateLimitWaiting) {
          if (rl.remainingSeconds > 0) {
            rl.remainingSeconds = Math.max(0, rl.remainingSeconds - 1);
          }
        }
      }, 1000);
    },

    // Data fetching
    async fetchTasks() {
      try {
        const res = await fetch('/api/tasks');
        const data = await res.json();
        this.tasks = Array.isArray(data) ? data : [];
      } catch (e) { console.error('Failed to fetch tasks:', e); }
    },
    async fetchActiveRuns() {
      try {
        const res = await fetch('/api/runs/active');
        const runs = await res.json();
        // Preserve existing runs that are still active
        const existing = new Map(this.activeRuns.map(r => [r.taskId, r]));
        this.activeRuns = runs.map(r => {
          return { ...r };
        });
        // Load logs for expanded or newly discovered runs
        for (const run of this.activeRuns) {
          if (!(this.runLogs[run.taskId] || []).length) {
            this.loadLog(run.taskId);
          }
        }
      } catch (e) { console.error('Failed to fetch active runs:', e); }
    },
    async fetchArchive() {
      try {
        const res = await fetch('/api/runs/archive');
        this.archive = await res.json();
      } catch (e) { console.error('Failed to fetch archive:', e); }
    },
    async fetchQueue() {
      try {
        const res = await fetch('/api/runs/queue');
        this.queuedTasks = await res.json();
      } catch (e) { console.error('Failed to fetch queue:', e); }
    },
    get filteredArchive() {
      if (this.archiveFilter === 'all') return this.archive;
      if (this.archiveFilter === 'done') return this.archive.filter(e => e.status === 'done');
      return this.archive.filter(e => e.status !== 'done');
    },

    // Sources
    async fetchSources() {
      try {
        const res = await fetch('/api/sources');
        this.sources = await res.json();
      } catch (e) { console.error('Failed to fetch sources:', e); }
    },
    async browseAndAddSource() {
      if (this.addSourceLoading) return;
      this.addSourceLoading = true;
      try {
        // Step 1: Pick folder
        const browseRes = await fetch('/api/browse-source', { method: 'POST' });
        if (!browseRes.ok) {
          const err = await browseRes.json().catch(() => ({ detail: 'Failed to browse' }));
          alert(err.detail || 'Failed to browse');
          this.addSourceLoading = false;
          return;
        }
        const browseData = await browseRes.json();
        if (browseData.cancelled) {
          this.addSourceLoading = false;
          return;
        }
        // Step 2: Ask for task ID prefix
        const taskPrefix = prompt('Task ID prefix for this source (e.g. MVP).\nLeave empty for no prefix:');
        if (taskPrefix === null) {
          // User cancelled the prompt
          this.addSourceLoading = false;
          return;
        }
        // Step 3: Create source
        const res = await fetch('/api/sources', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: browseData.path, task_prefix: taskPrefix.trim() }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: 'Failed to add source' }));
          alert(err.detail || 'Failed to add source');
        } else {
          await Promise.all([this.fetchSources(), this.fetchTasks()]);
        }
      } catch (e) {
        console.error('Failed to browse source:', e);
      }
      this.addSourceLoading = false;
    },
    confirmRemoveSource(sourceId, sourceName) {
      this.confirmRemoveSourceId = sourceId;
      this.confirmRemoveSourceName = sourceName;
    },
    async removeSource(sourceId) {
      try {
        const res = await fetch(`/api/sources/${sourceId}`, { method: 'DELETE' });
        if (res.ok) {
          if (this.activeSourceId === sourceId) {
            this.activeSourceId = null;
          }
          await Promise.all([this.fetchSources(), this.fetchTasks()]);
        }
      } catch (e) { console.error('Failed to remove source:', e); }
    },

    // Sorting & filtering
    // Called directly from x-for — Alpine.js tracks all reactive property
    // reads inside the method and re-renders when any of them change.
    getFilteredTasks() {
      let list = Array.isArray(this.tasks) ? [...this.tasks] : [];
      if (this.sortColumn && this.sortDirection) {
        const col = this.sortColumn;
        const dir = this.sortDirection === 'asc' ? 1 : -1;
        const getter = {
          id: t => t.id,
          title: t => (t.title || '').toLowerCase(),
          source: t => t.source,
          phase: t => t.phase || '',
          priority: t => t.priority,
          complexity: t => t.complexity,
          specStatus: t => t.specStatus,
          humanInput: t => t.humanInput,
        }[col] || (t => '');
        list.sort((a, b) => {
          const va = getter(a), vb = getter(b);
          if (va < vb) return -1 * dir;
          if (va > vb) return 1 * dir;
          return 0;
        });
      }
      if (this.activeSourceId) {
        list = list.filter(t => t.sourceId === this.activeSourceId);
      }
      if (this.selectedTaskId) {
        list = list.filter(t => t.id === this.selectedTaskId);
      }
      return list;
    },

    toggleSort(column) {
      if (this.sortColumn !== column) {
        this.sortColumn = column;
        this.sortDirection = 'asc';
      } else if (this.sortDirection === 'asc') {
        this.sortDirection = 'desc';
      } else {
        this.sortColumn = null;
        this.sortDirection = null;
      }
    },

    sortIcon(column) {
      if (this.sortColumn !== column) return '';
      return this.sortDirection === 'asc' ? ' \u2191' : ' \u2193';
    },

    // Task selection
    selectTask(task) {
      if (this.selectedTaskId === task.id) {
        this.selectedTaskId = null;
        this.selectedSpec = null;
        this.selectedSourceId = null;
        this.resetSpecEditor();
        return;
      }
      this.selectedTaskId = task.id;
      this.selectedSourceId = task.sourceId || null;
      this.resetSpecEditor();
      this.loadSpec(task.id, task.sourceId);
    },
    async loadSpec(taskId, sourceId) {
      this.specLoading = true;
      this.selectedSpec = null;
      try {
        const params = sourceId ? `?sourceId=${sourceId}` : '';
        const res = await fetch(`/api/tasks/${taskId}/spec${params}`);
        this.selectedSpec = await res.json();
      } catch (e) { console.error('Failed to load spec:', e); }
      this.specLoading = false;
    },

    // Spec editor
    get currentSpecContent() {
      return this.specEditedContent ?? (this.selectedSpec?.content || '');
    },
    get hasSpecEdits() {
      return this.specEditedContent !== null;
    },
    resetSpecEditor() {
      this.specEditInstruction = '';
      this.specEditLoading = false;
      this.specEditError = null;
      this.specEditedContent = null;
      this.specOriginalContent = null;
    },
    async submitSpecEdit() {
      const instruction = this.specEditInstruction.trim();
      if (!instruction || this.specEditLoading) return;
      this.specEditLoading = true;
      this.specEditError = null;
      // Save original on first edit
      if (this.specOriginalContent === null) {
        this.specOriginalContent = this.selectedSpec?.content || '';
      }
      try {
        const res = await fetch(`/api/tasks/${this.selectedTaskId}/spec/edit`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            content: this.currentSpecContent,
            instruction,
          }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: 'Request failed' }));
          this.specEditError = err.detail || 'AI editing failed';
        } else {
          const data = await res.json();
          this.specEditedContent = data.content;
          this.specEditInstruction = '';
        }
      } catch (e) {
        this.specEditError = 'Network error: ' + e.message;
      }
      this.specEditLoading = false;
    },
    async acceptSpecEdits() {
      if (!this.hasSpecEdits || !this.selectedSpec?.specPath) return;
      try {
        const res = await fetch(`/api/tasks/${this.selectedTaskId}/spec/save`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            content: this.specEditedContent,
            specPath: this.selectedSpec.specPath,
          }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: 'Save failed' }));
          this.specEditError = err.detail || 'Failed to save spec';
          return;
        }
        // Update the spec content in place and reset editor
        this.selectedSpec.content = this.specEditedContent;
        this.resetSpecEditor();
      } catch (e) {
        this.specEditError = 'Network error: ' + e.message;
      }
    },
    rejectSpecEdits() {
      this.specEditedContent = null;
      this.specOriginalContent = null;
      this.specEditError = null;
    },

    // Git
    async fetchGitStatus() {
      try {
        const res = await fetch('/api/git/status');
        const data = await res.json();
        this.currentBranch = data.branch;
      } catch (e) { console.error('Failed to fetch git status:', e); }
    },
    async checkoutBranch(branch) {
      if (this.checkoutLoading) return;
      this.checkoutLoading = true;
      try {
        const res = await fetch('/api/git/checkout', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ branch }),
        });
        if (res.ok) {
          this.currentBranch = branch;
        } else {
          const err = await res.json().catch(() => ({ detail: 'Checkout failed' }));
          alert(err.detail || 'Checkout failed');
        }
      } catch (e) { console.error('Failed to checkout:', e); }
      this.checkoutLoading = false;
    },
    async loadFiles(entry) {
      if (entry._files) {
        entry._files = null;
        return;
      }
      if (!entry.branch) return;
      entry._filesLoading = true;
      try {
        const res = await fetch(`/api/git/files/${entry.branch}`);
        const data = await res.json();
        entry._files = data.files;
      } catch (e) { console.error('Failed to load files:', e); }
      entry._filesLoading = false;
    },

    // Launch
    async launchAgent() {
      if (!this.selectedTaskId) return;
      const task = this.tasks.find(t => t.id === this.selectedTaskId);
      try {
        const res = await fetch('/api/runs/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            taskId: this.selectedTaskId,
            title: task?.title || '',
            source: task?.source || '',
            sourceId: this.selectedSourceId || task?.sourceId || 'default',
          }),
        });
        if (!res.ok) {
          const err = await res.json();
          alert(err.detail || 'Failed to start agent');
          return;
        }
        const data = await res.json();
        if (data.queued) {
          // Task was queued instead of started
          this.fetchQueue();
          this.launchOpen = false;
          return;
        }
        this.activeRuns.push({ ...data, elapsedSeconds: 0, alive: true });
        this.runLogs[data.taskId] = [];
        this.renderedLogHtml[data.taskId] = '';
        this.expandedRuns[data.taskId] = true;
        this.launchOpen = false;
        // Subscribe to this task's events
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
          this.ws.send(JSON.stringify({ action: 'subscribe', taskId: data.taskId }));
        }
      } catch (e) { console.error('Failed to launch agent:', e); }
    },

    async loadLog(taskId) {
      try {
        const res = await fetch(`/api/runs/${taskId}/log?tail=500`);
        const data = await res.json();
        if (data.lines && data.lines.length) {
          // Replace fully — REST is the source of truth for log content
          this.runLogs[taskId] = data.lines;
          this.scheduleLogRender(taskId);
        }
      } catch (e) { console.error('Failed to load log:', e); }
    },

    scheduleLogRender(taskId) {
      if (this._logRenderTimers[taskId]) return; // already scheduled
      this._logRenderTimers[taskId] = setTimeout(() => {
        delete this._logRenderTimers[taskId];
        const lines = this.runLogs[taskId] || [];
        const text = lines.slice(-500).join('\n');
        const escaped = text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        this.renderedLogHtml[taskId] = '<pre style="white-space:pre-wrap;word-break:break-word;margin:0;font-size:0.82em;line-height:1.5">' + escaped + '</pre>';
      }, 300);
    },

    isTaskRunningById(taskId) {
      return this.activeRuns.some(r => r.taskId === taskId);
    },
    isOnCooldown(taskId) {
      const task = this.tasks.find(t => t.id === taskId);
      return task && task.onCooldown;
    },

    // Stop
    async stopAgent(taskId) {
      try {
        await fetch(`/api/runs/${taskId}/stop`, { method: 'POST' });
        this.activeRuns = this.activeRuns.filter(r => r.taskId !== taskId);
        this.fetchArchive();
      } catch (e) { console.error('Failed to stop agent:', e); }
    },

    // Queue
    async cancelQueuedTask(itemId) {
      try {
        const res = await fetch(`/api/runs/queue/${itemId}`, { method: 'DELETE' });
        if (res.ok) {
          this.queuedTasks = this.queuedTasks.filter(q => (q.taskId || q.groupId) !== itemId);
        }
      } catch (e) { console.error('Failed to cancel queued item:', e); }
    },

    // Groups
    async fetchGroups() {
      try {
        const res = await fetch('/api/groups');
        this.groups = await res.json();
      } catch (e) { console.error('Failed to fetch groups:', e); }
    },
    toggleGroupSelectionMode() {
      this.groupSelectionMode = !this.groupSelectionMode;
      if (!this.groupSelectionMode) this.selectedForGroup = [];
    },
    toggleGroupSelection(taskId) {
      const idx = this.selectedForGroup.indexOf(taskId);
      if (idx >= 0) this.selectedForGroup.splice(idx, 1);
      else this.selectedForGroup.push(taskId);
    },
    moveGroupTask(idx, dir) {
      const arr = this.selectedForGroup;
      const newIdx = idx + dir;
      if (newIdx < 0 || newIdx >= arr.length) return;
      [arr[idx], arr[newIdx]] = [arr[newIdx], arr[idx]];
    },
    removeFromGroup(idx) {
      this.selectedForGroup.splice(idx, 1);
    },
    getTaskTitle(taskId) {
      const t = this.tasks.find(x => x.id === taskId);
      return t?.title || taskId;
    },
    async createGroup() {
      if (!this.newGroupName.trim() || this.selectedForGroup.length < 1) return;
      const tasks = this.selectedForGroup.map(id => {
        const t = this.tasks.find(x => x.id === id);
        return { taskId: id, title: t?.title || '', source: t?.source || '', sourceId: t?.sourceId || 'default' };
      });
      try {
        await fetch('/api/groups', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: this.newGroupName, tasks }),
        });
      } catch (e) { console.error('Failed to create group:', e); }
      this.groupCreateOpen = false;
      this.groupSelectionMode = false;
      this.selectedForGroup = [];
      this.newGroupName = '';
      await this.fetchGroups();
    },
    async startGroup(groupId) {
      try {
        const res = await fetch(`/api/groups/${groupId}/start`, { method: 'POST' });
        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: 'Failed to start group' }));
          // If 409 (agent running), offer to enqueue
          if (res.status === 409 && confirm((err.detail || '') + '\n\nQueue the group instead?')) {
            const res2 = await fetch(`/api/groups/${groupId}/start`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ enqueue: true }),
            });
            if (!res2.ok) {
              const err2 = await res2.json().catch(() => ({ detail: 'Failed' }));
              alert(err2.detail || 'Failed to enqueue group');
            }
          } else if (res.status !== 409) {
            alert(err.detail || 'Failed to start group');
          }
        }
      } catch (e) { console.error('Failed to start group:', e); }
      await Promise.all([this.fetchGroups(), this.fetchActiveRuns(), this.fetchQueue()]);
    },
    async stopGroup(groupId) {
      try {
        await fetch(`/api/groups/${groupId}/stop`, { method: 'POST' });
      } catch (e) { console.error('Failed to stop group:', e); }
      await Promise.all([this.fetchGroups(), this.fetchActiveRuns()]);
    },
    async continueGroup(groupId) {
      try {
        const res = await fetch(`/api/groups/${groupId}/continue`, { method: 'POST' });
        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: 'Failed' }));
          alert(err.detail || 'Failed to continue group');
        }
      } catch (e) { console.error('Failed to continue group:', e); }
      await Promise.all([this.fetchGroups(), this.fetchActiveRuns()]);
    },
    async retryGroupTask(groupId) {
      try {
        const res = await fetch(`/api/groups/${groupId}/retry`, { method: 'POST' });
        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: 'Failed' }));
          alert(err.detail || 'Failed to retry');
        }
      } catch (e) { console.error('Failed to retry group task:', e); }
      await Promise.all([this.fetchGroups(), this.fetchActiveRuns()]);
    },
    async deleteGroup(groupId) {
      try {
        await fetch(`/api/groups/${groupId}`, { method: 'DELETE' });
      } catch (e) { console.error('Failed to delete group:', e); }
      await this.fetchGroups();
    },

    // Schedules
    async fetchSchedules() {
      try {
        const res = await fetch('/api/schedule');
        this.schedules = await res.json();
      } catch (e) { console.error('Failed to fetch schedules:', e); }
    },
    get pendingSchedules() {
      return this.schedules.filter(s => s.status === 'pending');
    },
    openScheduleModal(type, taskId, groupId, title) {
      this.scheduleTarget = { type, taskId, groupId, title };
      this.scheduleMode = 'delay';
      this.scheduleDelayMinutes = 30;
      this.scheduleFixedTime = '';
      this.scheduleModalOpen = true;
    },
    async createSchedule() {
      if (!this.scheduleTarget) return;
      const body = {
        type: this.scheduleTarget.type,
        title: this.scheduleTarget.title || '',
      };
      if (this.scheduleTarget.taskId) body.taskId = this.scheduleTarget.taskId;
      if (this.scheduleTarget.groupId) body.groupId = this.scheduleTarget.groupId;
      if (this.scheduleMode === 'delay') {
        body.delaySeconds = this.scheduleDelayMinutes * 60;
      } else {
        body.fireAt = this.scheduleFixedTime;
      }
      try {
        const res = await fetch('/api/schedule', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: 'Failed' }));
          alert(err.detail || 'Failed to create schedule');
          return;
        }
      } catch (e) { console.error('Failed to create schedule:', e); }
      this.scheduleModalOpen = false;
      this.scheduleTarget = null;
      await this.fetchSchedules();
    },
    async cancelSchedule(scheduleId) {
      try {
        await fetch(`/api/schedule/${scheduleId}`, { method: 'DELETE' });
      } catch (e) { console.error('Failed to cancel schedule:', e); }
      await this.fetchSchedules();
    },
    formatCountdown(seconds) {
      if (!seconds || seconds <= 0) return '0:00';
      const h = Math.floor(seconds / 3600);
      const m = Math.floor((seconds % 3600) / 60);
      const s = Math.floor(seconds % 60);
      if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
      return `${m}:${s.toString().padStart(2, '0')}`;
    },

    // Delete Spec
    async deleteSpec() {
      if (!this.confirmDeleteSpec) return;
      const { taskId, sourceId } = this.confirmDeleteSpec;
      this.deleteSpecLoading = true;
      try {
        const params = sourceId ? `?sourceId=${sourceId}` : '';
        const res = await fetch(`/api/tasks/${taskId}/spec${params}`, { method: 'DELETE' });
        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: 'Failed' }));
          alert(err.detail || 'Failed to delete spec');
        } else {
          this.selectedTaskId = null;
          this.selectedSpec = null;
          this.resetSpecEditor();
          await this.fetchTasks();
        }
      } catch (e) { console.error('Failed to delete spec:', e); }
      this.deleteSpecLoading = false;
      this.confirmDeleteSpec = null;
    },

    // Rate Limit Waiting
    async fetchRateLimitWaiting() {
      try {
        const res = await fetch('/api/runs/rate-limit-waiting');
        this.rateLimitWaiting = await res.json();
      } catch (e) { console.error('Failed to fetch rate limit waiting:', e); }
    },

    taskGroupInfo(taskId) {
      for (const g of this.groups) {
        const idx = g.tasks.findIndex(t => t.task_id === taskId);
        if (idx >= 0) {
          const colors = {
            idle: 'bg-gray-800 text-gray-400',
            running: 'bg-amber-900/40 text-amber-400 border border-amber-800/30',
            paused: 'bg-blue-900/40 text-blue-400 border border-blue-800/30',
            completed: 'bg-emerald-900/40 text-emerald-400 border border-emerald-800/30',
            stopped: 'bg-red-900/40 text-red-400 border border-red-800/30',
          };
          return {
            label: `${g.name} #${idx + 1}`,
            color: colors[g.status] || 'bg-gray-800 text-gray-400',
          };
        }
      }
      return null;
    },
    groupProgress(group) {
      const done = Object.values(group.task_results || {}).filter(r => r.status === 'done').length;
      return `${done}/${group.tasks.length} done`;
    },
    groupStatusBadge(status) {
      const map = {
        idle: 'bg-gray-800 text-gray-400',
        running: 'bg-amber-900/40 text-amber-400',
        paused: 'bg-blue-900/40 text-blue-400',
        completed: 'bg-emerald-900/40 text-emerald-400',
        stopped: 'bg-red-900/40 text-red-400',
      };
      return map[status] || 'bg-gray-800 text-gray-400';
    },

    // Artifacts
    async loadArtifact(taskId, art) {
      if (this.selectedArtifact?.path === art.path) {
        this.selectedArtifact = null;
        return;
      }
      this.artifactLoading = true;
      this.selectedArtifact = { ...art, taskId, content: '' };
      try {
        const res = await fetch(`/api/runs/${taskId}/artifact/${art.path}`);
        const data = await res.json();
        this.selectedArtifact = { ...art, taskId, content: data.content, type: data.type || art.type || 'text' };
      } catch (e) { console.error('Failed to load artifact:', e); }
      this.artifactLoading = false;
    },

    // WebSocket
    connectWS() {
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      this.ws = new WebSocket(`${proto}//${location.host}/ws`);

      this.ws.onopen = () => {
        this.wsConnected = true;
        try { this.ws.send(JSON.stringify({ action: 'subscribe_all' })); } catch (e) {}
      };

      this.ws.onclose = () => {
        this.wsConnected = false;
        setTimeout(() => this.connectWS(), 3000);
      };

      this.ws.onerror = () => {
        this.wsConnected = false;
      };

      this.ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          this.handleWSMessage(msg);
        } catch (e) {}
      };
    },

    handleWSMessage(msg) {
      const taskId = msg.taskId;

      switch (msg.type) {
        case 'log':
          if (!this.runLogs[taskId]) this.runLogs[taskId] = [];
          this.runLogs[taskId].push(msg.line);
          // Keep last 1000 lines
          if (this.runLogs[taskId].length > 1000) {
            this.runLogs[taskId] = this.runLogs[taskId].slice(-500);
          }
          this.scheduleLogRender(taskId);
          break;

        case 'status':
          const run = this.activeRuns.find(r => r.taskId === taskId);
          if (run) {
            run.status = msg.status;
            if (msg.costUsd !== undefined) run.costUsd = msg.costUsd;
            if (msg.currentStep !== undefined) run.currentStep = msg.currentStep;
          }
          break;

        case 'queued':
          this.fetchQueue();
          break;

        case 'dequeued':
          const dqId = msg.itemId || taskId;
          this.queuedTasks = this.queuedTasks.filter(q => (q.taskId || q.groupId) !== dqId);
          break;

        case 'started':
          // Remove from queue if it was there (auto-started)
          this.queuedTasks = this.queuedTasks.filter(q => q.taskId !== taskId);
          if (!this.activeRuns.find(r => r.taskId === taskId)) {
            this.fetchActiveRuns();
          }
          break;

        case 'stopped':
        case 'crashed':
        case 'completed':
          this.activeRuns = this.activeRuns.filter(r => r.taskId !== taskId);
          this.fetchArchive();
          this.fetchQueue();
          break;

        case 'group_started':
        case 'group_continued':
        case 'group_completed':
        case 'group_stopped':
        case 'group_progress':
          this.fetchGroups();
          this.fetchActiveRuns();
          break;

        case 'scheduled':
        case 'schedule_cancelled':
          this.fetchSchedules();
          break;

        case 'schedule_fired_queued':
        case 'schedule_fired_started':
          this.fetchSchedules();
          this.fetchActiveRuns();
          this.fetchQueue();
          this.fetchGroups();
          break;

        case 'rate_limit_waiting':
          this.fetchRateLimitWaiting();
          this.fetchActiveRuns();
          break;

        case 'spec_deleted':
          this.fetchTasks();
          if (this.selectedTaskId === taskId) {
            this.selectedTaskId = null;
            this.selectedSpec = null;
            this.resetSpecEditor();
          }
          break;
      }
    },

    // Formatting helpers
    typeBadge(source) {
      const map = {
        'feature': 'bg-blue-900/40 text-blue-400 border border-blue-800/30',
        'tech-debt': 'bg-amber-900/40 text-amber-400 border border-amber-800/30',
        'refactor': 'bg-purple-900/40 text-purple-400 border border-purple-800/30',
        'audit': 'bg-emerald-900/40 text-emerald-400 border border-emerald-800/30',
      };
      return map[source] || 'bg-gray-800 text-gray-400';
    },

    specBadge(status) {
      const map = {
        'full': 'bg-emerald-900/30 text-emerald-400',
        'partial': 'bg-amber-900/30 text-amber-400',
        'stub': 'bg-red-900/30 text-red-400',
        'missing': 'bg-gray-800 text-gray-500',
      };
      return map[status] || 'bg-gray-800 text-gray-500';
    },

    humanBadge(input) {
      const map = {
        'auto': 'bg-emerald-900/20 text-emerald-500/70',
        'design': 'bg-yellow-900/30 text-yellow-400',
        'decision': 'bg-red-900/30 text-orange-400',
      };
      return map[input] || '';
    },

    statusColor(status) {
      const map = {
        'done': 'text-emerald-400',
        'failed': 'text-red-400',
        'interrupted': 'text-amber-400',
        'rate_limited': 'text-yellow-400',
        'running': 'text-amber-400',
        'reviewing': 'text-blue-400',
      };
      return map[status] || 'text-muted';
    },

    runStatus(run) {
      // Try to get status from state watcher updates
      return run.status || 'running';
    },

    formatDuration(seconds) {
      if (!seconds || seconds < 0) return '0:00';
      const m = Math.floor(seconds / 60);
      const s = Math.floor(seconds % 60);
      return `${m}:${s.toString().padStart(2, '0')}`;
    },

    formatDate(dateStr) {
      if (!dateStr) return '';
      try {
        const d = new Date(dateStr);
        return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
      } catch { return dateStr; }
    },
    formatDateTime(dateStr) {
      if (!dateStr) return '';
      try {
        const d = new Date(dateStr);
        const now = new Date();
        const isToday = d.toDateString() === now.toDateString();
        const time = d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', hour12: false });
        if (isToday) return time;
        return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ' ' + time;
      } catch { return dateStr; }
    },

    renderMarkdown(text) {
      if (!text) return '';
      try {
        return marked.parse(text);
      } catch { return text; }
    },
  };
}
