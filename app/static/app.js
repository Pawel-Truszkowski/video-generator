(() => {
  // State
  let files = [];
  let jobId = null;
  let eventSource = null;
  let currentScenes = [];
  let currentUser = null;
  // Credit balance and the current plan's cost, both from the server ONLY. Until
  // 0.6.0 app.js priced the plan from its own copy of the rate table and showed
  // the wrong model's rate after a resume — cosmetic while the number was an
  // estimate, a complaint once it leaves the account.
  let currentBalance = 0;
  let currentCost = null;
  // How many source images the job has, per the API: `files` is empty after a
  // resume, so the image picker has to count from the server instead.
  let imageCount = 0;
  // Scenes as shown on the progress screen. Separate from currentScenes (the
  // plan editor's working copy) because this one carries status/error and is
  // refreshed by SSE rather than edited by the user.
  let progressScenes = [];
  // The manual plan being written on the upload screen, before any job exists.
  // Same shape as currentScenes; it becomes the job's plan in one POST /jobs.
  let draftScenes = [];
  // One object URL per File: renderers run on every keystroke-level structure
  // change, and a fresh createObjectURL each time would leak a blob per render.
  const fileUrls = new WeakMap();

  // Elements
  const dropzone = document.getElementById('dropzone');
  const fileInput = document.getElementById('file-input');
  const previews = document.getElementById('previews');
  const promptEl = document.getElementById('prompt');
  const modelEl = document.getElementById('model');
  const durationEl = document.getElementById('duration');
  const durationLabel = document.getElementById('duration-label');
  const btnPlan = document.getElementById('btn-plan');
  const btnGenerate = document.getElementById('btn-generate');
  const btnBack = document.getElementById('btn-back');
  const btnAddScene = document.getElementById('btn-add-scene');
  const draftGroup = document.getElementById('draft-group');
  const draftScenesEl = document.getElementById('draft-scenes');
  const draftWarning = document.getElementById('draft-warning');
  const durationGroup = document.getElementById('duration-group');
  const promptLabel = document.getElementById('prompt-label');
  const planModeInputs = document.querySelectorAll('input[name="plan-mode"]');

  const screenForm = document.getElementById('screen-form');
  const screenPlan = document.getElementById('screen-plan');
  const screenProgress = document.getElementById('screen-progress');
  const scenesList = document.getElementById('scenes-list');
  const costAmount = document.getElementById('cost-amount');
  const totalDuration = document.getElementById('total-duration');
  const statusText = document.getElementById('status-text');
  const progressBar = document.getElementById('progress-bar');
  const progressDetail = document.getElementById('progress-detail');
  const errorBox = document.getElementById('error-box');
  const sceneProgress = document.getElementById('scene-progress');
  const videoResult = document.getElementById('video-result');
  const finalVideo = document.getElementById('final-video');
  const downloadLink = document.getElementById('download-link');

  // Auth + "Moje filmy"
  const userBar = document.getElementById('user-bar');
  const userEmail = document.getElementById('user-email');
  const btnMyVideos = document.getElementById('btn-my-videos');
  const btnNewVideo = document.getElementById('btn-new-video');
  const btnLogout = document.getElementById('btn-logout');
  const screenLogin = document.getElementById('screen-login');
  const screenJobs = document.getElementById('screen-jobs');
  const loginEmail = document.getElementById('login-email');
  const btnLogin = document.getElementById('btn-login');
  const loginInfo = document.getElementById('login-info');
  const loginDevlink = document.getElementById('login-devlink');
  const loginError = document.getElementById('login-error');
  const jobsList = document.getElementById('jobs-list');
  const jobsEmpty = document.getElementById('jobs-empty');
  const btnJobsNew = document.getElementById('btn-jobs-new');

  // Admin panel (Faza 2.3)
  const btnAdmin = document.getElementById('btn-admin');
  const adminJobs = document.getElementById('admin-jobs');
  const adminJobsEmpty = document.getElementById('admin-jobs-empty');
  const adminUsers = document.getElementById('admin-users');
  const adminStatusFilter = document.getElementById('admin-status-filter');

  // Credits (Faza 3)
  const btnCredits = document.getElementById('btn-credits');
  const balanceAmount = document.getElementById('balance-amount');
  const costBalance = document.getElementById('cost-balance');
  const btnTopup = document.getElementById('btn-topup');
  const creditsBalance = document.getElementById('credits-balance');
  const creditsBalanceUsd = document.getElementById('credits-balance-usd');
  const packagesList = document.getElementById('packages-list');
  const packagesOff = document.getElementById('packages-off');
  const creditsHistory = document.getElementById('credits-history');
  const creditsHistoryEmpty = document.getElementById('credits-history-empty');
  const btnCreditsBack = document.getElementById('btn-credits-back');

  // Screens are mutually exclusive; showScreen is the only thing that toggles them.
  const SCREEN_IDS = ['screen-login', 'screen-form', 'screen-plan', 'screen-progress', 'screen-jobs', 'screen-credits', 'screen-admin'];

  function showScreen(id) {
    SCREEN_IDS.forEach(s => {
      document.getElementById(s).classList.toggle('hidden', s !== id);
    });
    userBar.classList.toggle('hidden', !currentUser);
    // Cosmetic only: every /admin route checks is_admin server-side.
    btnAdmin.classList.toggle('hidden', !(currentUser && currentUser.is_admin));
  }

  // Every authenticated call goes through this so a 401 can never fail silently.
  async function apiFetch(url, opts) {
    const res = await fetch(url, opts);
    if (res.status === 401) {
      onLoggedOut();
      throw new Error('UNAUTHORIZED');
    }
    return res;
  }

  // FastAPI returns {"detail": ...}: a string for HTTPException, but an array of
  // validation objects for 422 — pull the message out of those rather than
  // dumping raw JSON at the user.
  async function errText(res) {
    try {
      const j = await res.json();
      if (typeof j.detail === 'string') return j.detail;
      if (Array.isArray(j.detail) && j.detail.length) {
        // Pydantic prefixes custom validator messages with "Value error, ".
        return j.detail
          .map(d => String(d.msg || '').replace(/^Value error,\s*/, ''))
          .filter(Boolean)
          .join('; ') || 'Nieprawidlowe dane.';
      }
      return JSON.stringify(j.detail || j);
    } catch (_) {
      return await res.text();
    }
  }

  function formatUsd(credits) {
    return '$' + (credits / 100).toFixed(2);
  }

  function setBalance(value) {
    currentBalance = Number(value) || 0;
    balanceAmount.textContent = currentBalance;
    creditsBalance.textContent = currentBalance;
    creditsBalanceUsd.textContent = '(' + formatUsd(currentBalance) + ')';
    updateCostBox();
  }

  /** Koszt planu wzgledem salda: decyduje, czy w ogole pokazac "Generuj". */
  function updateCostBox() {
    costAmount.textContent = currentCost == null ? '-' : currentCost;
    const enough = currentCost == null || currentCost <= currentBalance;
    costBalance.textContent = `(masz ${currentBalance})`;
    costBalance.classList.toggle('short', !enough);
    // Same pattern as hiding "Wznow" on files_purged: do not offer a button the
    // server will refuse. The 402 stays as a backstop, since another job may have
    // eaten the balance in the meantime.
    btnGenerate.classList.toggle('hidden', !enough);
    btnTopup.classList.toggle('hidden', enough);
  }

  function setCost(credits) {
    currentCost = credits == null ? null : Number(credits);
    updateCostBox();
  }

  function onLoggedOut() {
    currentUser = null;
    if (eventSource) { eventSource.close(); eventSource = null; }
    // Must clear staged work too, or the next user on this browser inherits the
    // previous user's uploads and job id.
    jobId = null;
    files = [];
    currentScenes = [];
    draftScenes = [];
    renderPreviews();
    renderDraft();
    updatePlanButton();
    showScreen('screen-login');
  }

  durationEl.addEventListener('input', () => {
    const v = parseInt(durationEl.value);
    const m = Math.floor(v / 60);
    const s = v % 60;
    durationLabel.textContent = m > 0 ? `${m}m ${s}s` : `${v}s`;
  });
  durationEl.dispatchEvent(new Event('input'));

  // Drag & drop
  dropzone.addEventListener('click', () => fileInput.click());
  dropzone.addEventListener('dragover', e => { e.preventDefault(); dropzone.classList.add('dragover'); });
  dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
  dropzone.addEventListener('drop', e => {
    e.preventDefault();
    dropzone.classList.remove('dragover');
    addFiles(e.dataTransfer.files);
  });
  fileInput.addEventListener('change', () => addFiles(fileInput.files));

  function addFiles(newFiles) {
    for (const f of newFiles) {
      if (files.length >= 30) break;
      if (!f.type.match(/^image\/(jpeg|png|webp)$/)) continue;
      files.push(f);
      // One scene per image by default: the common case is "each photo is a shot".
      draftScenes.push({
        image_index: files.length - 1, sub_prompt: '', duration_s: 5, chain_from_prev: false,
      });
    }
    renderPreviews();
    renderDraft();
    updatePlanButton();
  }

  function fileUrl(f) {
    if (!fileUrls.has(f)) fileUrls.set(f, URL.createObjectURL(f));
    return fileUrls.get(f);
  }

  /**
   * Drop image `removed` from the draft: its own scene goes, together with the
   * continuations hanging off it (they would silently start chaining from a
   * different shot), and every later image_index shifts down by one.
   */
  function removeImageFromDraft(removed) {
    const kept = [];
    let dropping = false;
    for (const s of draftScenes) {
      if (!s.chain_from_prev) dropping = s.image_index === removed;
      if (!dropping) kept.push(s);
    }
    kept.forEach(s => { if (s.image_index > removed) s.image_index -= 1; });
    if (kept.length) kept[0].chain_from_prev = false;
    draftScenes = kept;
  }

  function planMode() {
    return document.querySelector('input[name="plan-mode"]:checked').value;
  }

  function planButtonLabel() {
    return planMode() === 'manual' ? 'Dalej' : 'Zaplanuj';
  }

  /** Show the parts of the upload screen that belong to the chosen mode. */
  function applyPlanMode() {
    const manual = planMode() === 'manual';
    draftGroup.classList.toggle('hidden', !manual);
    durationGroup.classList.toggle('hidden', manual);
    promptLabel.textContent = manual
      ? 'Styl (opcjonalnie, dodawany do kazdej sceny)'
      : 'Opis filmu';
    promptEl.placeholder = manual
      ? 'np. realistic footage, stable camera, soft studio lighting'
      : 'Opisz jaki film chcesz wygenerowac...';
    btnPlan.textContent = planButtonLabel();
    updatePlanButton();
  }
  planModeInputs.forEach(r => r.addEventListener('change', applyPlanMode));
  applyPlanMode();

  function renderDraft() {
    renderSceneCards(draftScenesEl, draftScenes, {
      imageSrc: i => (files[i] ? fileUrl(files[i]) : ''),
      imageCount: files.length,
      onInput: updatePlanButton,
      onFieldChange: () => {},
      onStructureChange: () => { renderDraft(); updatePlanButton(); },
    });

    // An image only reaches the film through a scene that starts from it;
    // a continuation starts from the previous clip's last frame instead.
    const used = new Set(draftScenes.filter(s => !s.chain_from_prev).map(s => s.image_index));
    const unused = files.map((_, i) => i).filter(i => !used.has(i));
    draftWarning.textContent = unused.length
      ? `Zdjecia ${unused.map(i => '#' + (i + 1)).join(', ')} nie startuja zadnej sceny i nie trafia do filmu.`
      : '';
    draftWarning.classList.toggle('hidden', !unused.length);
  }

  function renderPreviews() {
    previews.innerHTML = '';
    files.forEach((f, i) => {
      const wrap = document.createElement('div');
      wrap.className = 'preview-item';
      const img = document.createElement('img');
      img.className = 'preview-thumb';
      img.src = fileUrl(f);
      const btn = document.createElement('button');
      btn.className = 'preview-remove';
      btn.textContent = '\u00d7';
      btn.onclick = () => {
        files.splice(i, 1);
        removeImageFromDraft(i);
        renderPreviews();
        renderDraft();
        updatePlanButton();
      };
      wrap.appendChild(img);
      wrap.appendChild(btn);
      previews.appendChild(wrap);
    });
  }

  function updatePlanButton() {
    if (planMode() === 'manual') {
      // The style is optional here; what the provider cannot do without is a
      // description of each scene's motion.
      btnPlan.disabled = files.length === 0 || draftScenes.length === 0
        || draftScenes.some(s => !s.sub_prompt.trim());
    } else {
      btnPlan.disabled = files.length === 0 || promptEl.value.trim() === '';
    }
  }
  promptEl.addEventListener('input', updatePlanButton);

  btnPlan.addEventListener('click', async () => {
    const manual = planMode() === 'manual';
    btnPlan.disabled = true;
    btnPlan.textContent = manual ? 'Wysylanie...' : 'Planowanie...';

    const fd = new FormData();
    files.forEach(f => fd.append('images', f));
    fd.append('prompt', promptEl.value.trim());
    fd.append('model', modelEl.value);
    fd.append('target_duration_s', durationEl.value);
    fd.append('plan_mode', planMode());
    if (manual) fd.append('scenes', JSON.stringify(draftScenes));

    try {
      const res = await apiFetch('/jobs', { method: 'POST', body: fd });
      if (!res.ok) throw new Error(await errText(res));
      const data = await res.json();
      jobId = data.job_id;

      connectSSE();
      if (manual) {
        // The job is already 'planned'. The scenes come back from the server
        // rather than from draftScenes, since it may have corrected them.
        imageCount = files.length;
        currentScenes = data.scenes;
        showPlanScreen(data.credits_cost);
        return;
      }
      const planRes = await apiFetch(`/jobs/${jobId}/plan`, { method: 'POST' });
      if (!planRes.ok) throw new Error(await errText(planRes));
    } catch (e) {
      if (e.message !== 'UNAUTHORIZED') {
        alert('Blad: ' + e.message);
        btnPlan.textContent = planButtonLabel();
        updatePlanButton();
      }
    }
  });

  function connectSSE() {
    if (eventSource) eventSource.close();
    eventSource = new EventSource(`/jobs/${jobId}/events`);

    eventSource.addEventListener('planned', e => {
      const data = JSON.parse(e.data);
      currentScenes = data.scenes;
      showPlanScreen(data.credits_cost);
    });

    eventSource.addEventListener('status', e => {
      const data = JSON.parse(e.data);
      statusText.textContent = translateStatus(data.status);
      updateProgressBar(data.status);
    });

    eventSource.addEventListener('scene', e => {
      const data = JSON.parse(e.data);
      // The event carries no sub_prompt, so the existing one is kept: an event
      // that beats the initial GET /jobs/{id} still renders, just untitled.
      progressScenes[data.idx] = {
        sub_prompt: progressScenes[data.idx]?.sub_prompt || '',
        status: data.status,
        error: data.error || null,
      };
      renderSceneProgress();
    });
    
    // Refund for scenes that failed. Emitted from the pipeline's `finally`, so it
    // arrives after a failed generation too, not only a successful one.
    eventSource.addEventListener('credits', e => {
      const data = JSON.parse(e.data);
      if (data.balance != null) setBalance(data.balance);
      if (data.refunded) {
        progressDetail.textContent =
          `Zwrocono ${data.refunded} kredytow za niewykonane sceny. ` +
          `Zuzyto ${data.spent}, saldo: ${currentBalance}.`;
      }
    });

    eventSource.addEventListener('done', e => {
      const data = JSON.parse(e.data);
      showDone(data.final_path);
    });

    eventSource.addEventListener('error', async e => {
      // This listener is overloaded: it fires both for pipeline errors (which
      // carry e.data) and for EventSource connection failures (which do not).
      if (e.data) {
        try {
          showError(JSON.parse(e.data).error);
        } catch (_) {}
        return;
      }
      // No payload + closed stream means the connection was rejected. If that
      // was a 401, fall back to the login screen instead of hanging forever.
      if (eventSource && eventSource.readyState === EventSource.CLOSED) {
        try {
          const res = await fetch('/auth/me');
          if (res.status === 401) onLoggedOut();
        } catch (_) {}
      }
    });
  }

  function translateStatus(s) {
    const map = {
      validating: 'Walidacja zdjec...',
      planning: 'Planowanie scen...',
      generating: 'Generowanie klipow...',
      stitching: 'Laczenie klipow...',
    };
    return map[s] || s;
  }

  function updateProgressBar(status) {
    const pct = { validating: 10, planning: 20, generating: 50, stitching: 85 };
    progressBar.style.width = (pct[status] || 0) + '%';
    progressDetail.textContent = translateStatus(status);
  }

  /** Total plan duration. The price is the server's — see setCost(). */
  function recalcDuration() {
    const totalS = currentScenes.reduce((sum, s) => sum + s.duration_s, 0);
    const m = Math.floor(totalS / 60);
    const s = totalS % 60;
    totalDuration.textContent = `(${m}m ${s}s)`;
  }

  function showPlanScreen(creditsCost) {
    showScreen('screen-plan');
    renderScenes();
    recalcDuration();
    setCost(creditsCost);
    btnPlan.textContent = planButtonLabel();
    updatePlanButton();
    // showProgressScreen() disables it, and a resume can land here afterwards.
    btnGenerate.disabled = false;
  }

  /** Local File while the upload is still in this tab, server copy after a resume. */
  function sceneImageSrc(imageIndex) {
    if (files[imageIndex]) return fileUrl(files[imageIndex]);
    if (jobId) return `/jobs/${jobId}/images/${imageIndex}`;
    return '';
  }

  /** The plan editor on screen 2: every change goes straight to the server. */
  function renderScenes() {
    renderSceneCards(scenesList, currentScenes, {
      imageSrc: sceneImageSrc,
      imageCount: files.length || imageCount,
      onFieldChange: i => { recalcDuration(); syncScenesToBackend(i); },
      // chain_from_prev changes how scenes group into chains, so it goes through
      // /scenes/sync like add/remove; /scenes/{idx}/update is per-field only.
      onStructureChange: () => { renderScenes(); recalcDuration(); syncAllScenes(); },
    });
  }

  /**
   * Scene cards, shared by the upload screen (draft, local only) and the plan
   * editor (synced). Edits mutate `scenes` in place; `opts` says what else to do:
   *   imageSrc(i)          thumbnail URL for image i
   *   imageCount           options in the image picker
   *   onInput()            optional, on every keystroke in a description
   *   onFieldChange(i)     a field of scene i changed (text, duration, image)
   *   onStructureChange()  scenes added/removed or chaining changed; re-render
   */
  function renderSceneCards(container, scenes, opts) {
    container.innerHTML = '';

    scenes.forEach((s, i) => {
      const card = document.createElement('div');
      card.className = 'scene-card' + (s.chain_from_prev ? ' chained' : '');

      // A continuation starts from the previous clip's last frame, not from its
      // image_index — show that instead of a picture that will not be used.
      const img = document.createElement('img');
      img.src = opts.imageSrc(s.image_index);
      img.title = s.chain_from_prev ? `Start z ostatniej klatki sceny ${i}` : '';

      const info = document.createElement('div');
      info.className = 'scene-info';

      const idx = document.createElement('div');
      idx.className = 'scene-idx';
      const label = document.createElement('span');
      label.textContent = `Scena ${i + 1}` + (s.chain_from_prev ? ' (kontynuacja)' : '');
      idx.appendChild(label);

      if (scenes.length > 1) {
        const removeBtn = document.createElement('button');
        removeBtn.className = 'scene-remove';
        removeBtn.textContent = 'Usun';
        removeBtn.onclick = () => {
          scenes.splice(i, 1);
          if (scenes.length) scenes[0].chain_from_prev = false;
          opts.onStructureChange();
        };
        idx.appendChild(removeBtn);
      }

      const ta = document.createElement('textarea');
      ta.value = s.sub_prompt;
      ta.placeholder = 'Co dzieje sie w tej scenie? Ruch kamery, akcja...';
      ta.addEventListener('input', () => {
        s.sub_prompt = ta.value;
        if (opts.onInput) opts.onInput();
      });
      ta.addEventListener('change', () => opts.onFieldChange(i));

      const controls = document.createElement('div');
      controls.className = 'scene-controls';

      const durLabel = document.createElement('label');
      durLabel.textContent = 'Czas:';
      const durSel = document.createElement('select');
      [5, 10].forEach(v => {
        const opt = document.createElement('option');
        opt.value = v; opt.textContent = v + 's';
        if (v === s.duration_s) opt.selected = true;
        durSel.appendChild(opt);
      });
      durSel.addEventListener('change', () => {
        s.duration_s = parseInt(durSel.value);
        opts.onFieldChange(i);
      });
      controls.appendChild(durLabel);
      controls.appendChild(durSel);

      if (s.chain_from_prev) {
        const hint = document.createElement('span');
        hint.className = 'scene-chain-hint';
        hint.textContent = `start z ostatniej klatki sceny ${i}`;
        controls.appendChild(hint);
      } else {
        const imgLabel = document.createElement('label');
        imgLabel.textContent = 'Zdjecie:';
        const imgSel = document.createElement('select');
        for (let fi = 0; fi < opts.imageCount; fi++) {
          const opt = document.createElement('option');
          opt.value = fi; opt.textContent = `#${fi + 1}`;
          if (fi === s.image_index) opt.selected = true;
          imgSel.appendChild(opt);
        }
        imgSel.addEventListener('change', () => {
          s.image_index = parseInt(imgSel.value);
          img.src = opts.imageSrc(s.image_index);
          opts.onFieldChange(i);
        });
        controls.appendChild(imgLabel);
        controls.appendChild(imgSel);
      }

      if (i > 0) {
        const chainLabel = document.createElement('label');
        chainLabel.className = 'scene-chain';
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = !!s.chain_from_prev;
        cb.addEventListener('change', () => {
          s.chain_from_prev = cb.checked;
          // Ignored by the pipeline for a continuation, but keeps the thumbnail
          // meaningful: the shot this scene grows out of.
          if (cb.checked) s.image_index = scenes[i - 1].image_index;
          opts.onStructureChange();
        });
        chainLabel.appendChild(cb);
        chainLabel.appendChild(document.createTextNode(' Kontynuuj poprzednia'));
        controls.appendChild(chainLabel);
      }

      const addCont = document.createElement('button');
      addCont.className = 'btn-link scene-add-cont';
      addCont.textContent = '+ kontynuacja';
      addCont.onclick = () => {
        scenes.splice(i + 1, 0, {
          image_index: s.image_index, sub_prompt: '', duration_s: 5, chain_from_prev: true,
        });
        opts.onStructureChange();
      };
      controls.appendChild(addCont);

      info.appendChild(idx);
      info.appendChild(ta);
      info.appendChild(controls);
      card.appendChild(img);
      card.appendChild(info);
      container.appendChild(card);
    });
  }

  btnAddScene.addEventListener('click', () => {
    const newScene = {
      image_index: 0,
      sub_prompt: 'Nowa scena - opisz ruch kamery...',
      duration_s: 5,
      chain_from_prev: false,
    };
    currentScenes.push(newScene);
    renderScenes();
    recalcDuration();
    syncAllScenes();
  });

  async function syncScenesToBackend(idx) {
    const s = currentScenes[idx];
    const fd = new FormData();
    fd.append('sub_prompt', s.sub_prompt);
    fd.append('duration_s', s.duration_s);
    fd.append('image_index', s.image_index);
    try {
      const res = await apiFetch(`/jobs/${jobId}/scenes/${idx}/update`, { method: 'POST', body: fd });
      // Until 0.6.0 this response was discarded and the UI showed its own sum.
      if (res.ok) setCost((await res.json()).credits_cost);
    } catch (_) {}  // apiFetch has already switched screens on 401
  }

  async function syncAllScenes() {
    try {
      const res = await apiFetch(`/jobs/${jobId}/scenes/sync`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scenes: currentScenes }),
      });
      if (res.ok) setCost((await res.json()).credits_cost);
    } catch (_) {}
  }

  btnGenerate.addEventListener('click', async () => {
    await syncAllScenes();

    showProgressScreen();

    try {
      const res = await apiFetch(`/jobs/${jobId}/generate`, { method: 'POST' });
      if (res.status === 402) {
        // Short credits is not a generation failure: go back to the plan, where
        // "Doladuj" lives, instead of stranding the user on the progress screen
        // with a red error and nothing to click.
        const msg = await errText(res);
        await refreshBalance();
        showPlanScreen(currentCost);
        planError(msg);
        return;
      }
      if (!res.ok) throw new Error(await errText(res));
    } catch (e) {
      if (e.message !== 'UNAUTHORIZED') showError(e.message);
    }
  });

  function planError(msg) {
    costBalance.textContent = msg;
    costBalance.classList.add('short');
  }

  const SCENE_STATUS_LABELS = {
    pending: 'Oczekuje', generating: 'Generowanie', done: 'Gotowe', error: 'Blad',
  };

  function renderSceneProgress() {
    sceneProgress.innerHTML = '';

    progressScenes.forEach((s, i) => {
      const row = document.createElement('div');
      row.className = 'scene-row';
      row.dataset.idx = i;

      const num = document.createElement('span');
      num.className = 'scene-num';
      num.textContent = `#${i + 1}`;

      const text = document.createElement('span');
      text.className = 'scene-text';
      text.textContent = s.sub_prompt || '';

      const badge = document.createElement('span');
      badge.className = 'status-badge ' + (s.status || 'pending');
      badge.textContent = SCENE_STATUS_LABELS[s.status] || s.status || '';

      row.appendChild(num);
      row.appendChild(text);

      if (s.status === 'error') {
        const err = document.createElement('span');
        err.className = 'scene-err';
        err.textContent = s.error || '';
        err.title = s.error || '';
        row.appendChild(err);

        const retry = document.createElement('button');
        retry.className = 'btn-link';
        retry.textContent = 'Ponow';
        retry.onclick = () => retryScene(i, retry);
        row.appendChild(retry);
      }

      row.appendChild(badge);
      sceneProgress.appendChild(row);
    });
  }

  /** Pull the authoritative scene list from the server. */
  async function loadSceneProgress() {
    if (!jobId) return;
    try {
      const res = await apiFetch(`/jobs/${jobId}`);
      if (!res.ok) return;
      const detail = await res.json();
      progressScenes = detail.scenes.map(sc => ({
        sub_prompt: sc.sub_prompt,
        status: sc.status,
        error: sc.error,
      }));
      renderSceneProgress();
    } catch (_) {}  // apiFetch already switched screens on 401
  }

  async function retryScene(idx, button) {
    button.disabled = true;
    button.textContent = 'Ponawianie...';
    try {
      const res = await apiFetch(`/jobs/${jobId}/scenes/${idx}/retry`, { method: 'POST' });
      if (!res.ok) throw new Error(await errText(res));
      errorBox.classList.add('hidden');
      statusText.textContent = 'Generowanie klipow...';
      statusText.className = 'status-badge generating';
      // The server reset the row before starting, so mirror that locally
      // instead of waiting for an event that only fires when it finishes.
      progressScenes[idx].status = 'pending';
      progressScenes[idx].error = null;
      renderSceneProgress();
    } catch (e) {
      if (e.message !== 'UNAUTHORIZED') {
        alert('Blad: ' + e.message);
        button.disabled = false;
        button.textContent = 'Ponow';
      }
    }
  }

  function showProgressScreen(text, badgeClass) {
    btnGenerate.disabled = true;
    showScreen('screen-progress');
    errorBox.classList.add('hidden');
    videoResult.classList.add('hidden');
    progressBar.style.width = '30%';
    statusText.textContent = text || 'Generowanie klipow...';
    statusText.className = 'status-badge ' + (badgeClass || 'generating');
    loadSceneProgress();
  }

  btnBack.addEventListener('click', () => {
    showScreen('screen-form');
  });

  function showDone(finalPath) {
    progressBar.style.width = '100%';
    statusText.textContent = 'Gotowe!';
    statusText.className = 'status-badge done';
    progressDetail.textContent = '';
    videoResult.classList.remove('hidden');
    finalVideo.src = finalPath;
    downloadLink.href = finalPath;
    if (eventSource) eventSource.close();
  }

  function showError(msg) {
    // Scenes that never ran (the tail of a broken chain) emit no event of their
    // own, so the list is re-read rather than patched from what arrived.
    loadSceneProgress();
    errorBox.textContent = 'Blad: ' + msg;
    errorBox.classList.remove('hidden');
    statusText.textContent = 'Blad';
    statusText.className = 'status-badge error';
    progressBar.style.width = '0%';
  }

  // ---------------------------------------------------------------- Auth ----

  async function doLogin() {
    const email = loginEmail.value.trim();
    loginError.classList.add('hidden');
    loginInfo.classList.add('hidden');
    loginDevlink.classList.add('hidden');

    if (!email) {
      loginError.textContent = 'Podaj adres email.';
      loginError.classList.remove('hidden');
      return;
    }

    btnLogin.disabled = true;
    btnLogin.textContent = 'Wysylanie...';
    try {
      const res = await fetch('/auth/request-login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      });
      if (!res.ok) {
        loginError.textContent = await errText(res);
        loginError.classList.remove('hidden');
        return;
      }
      const data = await res.json();
      loginInfo.textContent = 'Wyslalismy link do logowania na podany adres. Sprawdz skrzynke.';
      loginInfo.classList.remove('hidden');
      // Only present when MAIL_PROVIDER=console — lets dev finish the loop in one click.
      if (data.login_url) {
        loginDevlink.href = data.login_url;
        loginDevlink.classList.remove('hidden');
      }
    } catch (e) {
      loginError.textContent = 'Blad polaczenia: ' + e.message;
      loginError.classList.remove('hidden');
    } finally {
      btnLogin.disabled = false;
      btnLogin.textContent = 'Wyslij link do logowania';
    }
  }

  btnLogin.addEventListener('click', doLogin);
  loginEmail.addEventListener('keydown', e => { if (e.key === 'Enter') doLogin(); });

  btnLogout.addEventListener('click', async () => {
    try { await fetch('/auth/logout', { method: 'POST' }); } catch (_) {}
    onLoggedOut();
  });

  // ---------------------------------------------------------- Moje filmy ----

  const STATUS_LABELS = {
    uploaded: 'Wgrane', planning: 'Planowanie', planned: 'Zaplanowane',
    generating: 'Generowanie', stitching: 'Laczenie', done: 'Gotowe', error: 'Blad',
    interrupted: 'Przerwane',
  };

  // 'done' is excluded on purpose: nothing left to run, and the card already
  // offers Odtworz/Pobierz.
  const RESUMABLE_STATUSES = ['uploaded', 'planned', 'interrupted', 'error'];

  // Mirrors STALE_STATUSES on the server: a live task owns these jobs' files.
  const BUSY_STATUSES = ['planning', 'generating', 'stitching'];

  function statusClass(s) {
    if (s === 'done') return 'done';
    if (s === 'error') return 'error';
    if (s === 'interrupted') return 'interrupted';
    if (s === 'uploaded' || s === 'planned') return 'planned';
    return 'generating';
  }

  function fmtDuration(totalS) {
    const m = Math.floor(totalS / 60);
    const s = totalS % 60;
    return m > 0 ? `${m}m ${s}s` : `${s}s`;
  }

  /** Open a job's progress screen read-only — no POST, nothing starts.

    This is the only way back to a finished or failed job's scene list, and so
    the only way to reach a single scene's "Ponow" after leaving the page:
    resumeJob() regenerates every failed scene at once by design.
   */
  function previewJob(job) {
    jobId = job.id;
    files = [];

    // Connected up front so a retry started from this screen streams live.
    connectSSE();

    showProgressScreen(STATUS_LABELS[job.status] || job.status, statusClass(job.status));
    // showProgressScreen assumes work in flight; nothing is running here.
    progressBar.style.width = job.status === 'done' ? '100%' : '0%';
    progressDetail.textContent = '';

    if (job.error) {
      errorBox.textContent = 'Blad: ' + job.error;
      errorBox.classList.remove('hidden');
    }
    if (job.video_url) {
      videoResult.classList.remove('hidden');
      finalVideo.src = job.video_url;
      downloadLink.href = job.video_url;
    }
  }

  /** Pick a job back up from wherever it stopped. */
  async function resumeJob(job) {
    jobId = job.id;
    // The uploads are gone from this tab; thumbnails now come from the server.
    files = [];

    let detail;
    try {
      const res = await apiFetch(`/jobs/${jobId}`);
      if (!res.ok) throw new Error(await errText(res));
      detail = await res.json();
    } catch (e) {
      if (e.message !== 'UNAUTHORIZED') alert('Blad: ' + e.message);
      return;
    }

    imageCount = detail.image_count;
    // Narrowed to the plan-editor shape: /scenes/sync rejects the extra
    // status/clip_path fields the DB rows carry.
    currentScenes = detail.scenes.map(sc => ({
      image_index: sc.image_index,
      sub_prompt: sc.sub_prompt,
      duration_s: sc.duration_s,
      chain_from_prev: !!sc.chain_from_prev,
    }));

    // Subscribe before asking the server to start, so no early event is missed.
    connectSSE();

    let stage;
    try {
      const res = await apiFetch(`/jobs/${jobId}/resume`, { method: 'POST' });
      if (!res.ok) throw new Error(await errText(res));
      stage = (await res.json()).stage;
    } catch (e) {
      if (e.message !== 'UNAUTHORIZED') alert('Blad: ' + e.message);
      return;
    }

    if (stage === 'planned') {
      // credits_pending, not credits_cost: scenes with a finished clip are paid
      // for, so the editor shows what FINISHING the job costs.
      showPlanScreen(detail.credits_pending);
    } else if (stage === 'generating') {
      showProgressScreen('Wznawianie generowania...');
    } else if (stage === 'planning') {
      // Planning is already running server-side and the SSE 'planned' event
      // will swap this for the editor; meanwhile the user sees the click landed.
      showProgressScreen('Planowanie scen...');
    } else if (stage === 'done') {
      showDone(`/media/${jobId}/final.mp4`);
    } else {
      // `stage` comes off the wire — never leave the user on a dead screen.
      alert('Nieznany etap wznowienia: ' + stage);
    }
  }

  /** Remove a job and its files for good. Server refuses while it is running. */
  async function deleteJob(job, card) {
    const label = job.prompt ? `"${job.prompt.slice(0, 60)}"` : 'ten job';
    if (!confirm(`Usunac ${label} wraz z plikami? Tej operacji nie da sie cofnac.`)) return;

    try {
      const res = await apiFetch(`/jobs/${job.id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(await errText(res));
    } catch (e) {
      if (e.message !== 'UNAUTHORIZED') alert('Blad: ' + e.message);
      return;
    }

    // Drop just this card instead of reloading the list: a full reload would
    // scroll the user back to the top of "Moje filmy" after every deletion.
    card.remove();
    if (jobId === job.id) { jobId = null; }
    if (!jobsList.childElementCount) jobsEmpty.classList.remove('hidden');
  }

  function renderJobCard(job) {
    const card = document.createElement('div');
    card.className = 'job-card';

    const img = document.createElement('img');
    img.className = 'job-thumb';
    img.loading = 'lazy';
    img.src = job.thumb_url;
    // Thumbnails legitimately 404 once uploads have been cleaned off disk.
    img.onerror = () => {
      const ph = document.createElement('div');
      ph.className = 'thumb-placeholder';
      ph.textContent = 'brak';
      img.replaceWith(ph);
    };

    const info = document.createElement('div');
    info.className = 'job-info';

    const prompt = document.createElement('div');
    prompt.className = 'job-prompt';
    // A manual plan may have no style, and then no prompt at all.
    prompt.textContent = job.prompt || `Plan reczny · ${job.scene_count} scen`;

    const meta = document.createElement('div');
    meta.className = 'job-meta';

    const badge = document.createElement('span');
    badge.className = 'status-badge ' + statusClass(job.status);
    badge.textContent = STATUS_LABELS[job.status] || job.status;
    meta.appendChild(badge);

    const date = document.createElement('span');
    // Correct only because the API returns ISO-8601 with a trailing Z.
    date.textContent = job.created_at ? new Date(job.created_at).toLocaleString('pl-PL') : '';
    meta.appendChild(date);

    if (job.est_cost_usd != null) {
      const cost = document.createElement('span');
      cost.textContent = '$' + Number(job.est_cost_usd).toFixed(2);
      meta.appendChild(cost);
    }

    if (job.scene_count) {
      const scenes = document.createElement('span');
      scenes.textContent = `${job.scene_count} scen / ${fmtDuration(job.total_duration_s || 0)}`;
      meta.appendChild(scenes);
    }

    info.appendChild(prompt);
    info.appendChild(meta);

    const actions = document.createElement('div');
    actions.className = 'job-actions';

    // Any job with a plan has scenes worth looking at, whatever its status.
    if (job.scene_count) {
      const preview = document.createElement('button');
      preview.className = 'btn-link';
      preview.textContent = 'Podglad';
      preview.onclick = () => previewJob(job);
      actions.appendChild(preview);
    }

    // Every path behind "Wznow" needs the source images, and the retention sweep
    // has taken them — the server would 409, so do not offer the button at all.
    if (job.files_purged) {
      const note = document.createElement('span');
      note.className = 'job-note';
      note.textContent = 'pliki usuniete';
      actions.appendChild(note);
    } else if (RESUMABLE_STATUSES.includes(job.status)) {
      const resume = document.createElement('button');
      resume.className = 'btn-link';
      resume.textContent = 'Wznow';
      resume.onclick = () => resumeJob(job);
      // The exact cost depends on which clips are on disk, which the job list
      // does not know. Zero is unambiguous though — it covers nothing — so the
      // button would only disappoint. Above zero the server decides (402 with
      // the actual amount).
      if (currentBalance <= 0) {
        resume.disabled = true;
        resume.title = 'Brak kredytow - doladuj konto';
      }
      actions.appendChild(resume);
    }

    if (job.video_url) {
      const play = document.createElement('button');
      play.className = 'btn-link';
      play.textContent = 'Odtworz';
      play.onclick = () => {
        if (card.querySelector('video')) return;
        const v = document.createElement('video');
        v.controls = true;
        v.src = job.video_url;
        info.appendChild(v);
        play.disabled = true;
      };

      const dl = document.createElement('a');
      dl.className = 'btn-link';
      dl.href = job.video_url;
      dl.download = '';
      dl.textContent = 'Pobierz MP4';

      actions.appendChild(play);
      actions.appendChild(dl);
    }

    // Hidden while a task owns the job's files — DELETE would 409 anyway.
    if (!BUSY_STATUSES.includes(job.status)) {
      const del = document.createElement('button');
      del.className = 'btn-link danger';
      del.textContent = 'Usun';
      del.onclick = () => deleteJob(job, card);
      actions.appendChild(del);
    }

    if (actions.childElementCount) info.appendChild(actions);

    card.appendChild(img);
    card.appendChild(info);
    return card;
  }

  async function loadJobs() {
    jobsList.innerHTML = '';
    jobsEmpty.classList.add('hidden');
    try {
      const res = await apiFetch('/jobs');
      if (!res.ok) return;
      const data = await res.json();
      if (!data.jobs.length) {
        jobsEmpty.classList.remove('hidden');
        return;
      }
      data.jobs.forEach(j => jobsList.appendChild(renderJobCard(j)));
    } catch (_) {}
  }

  btnMyVideos.addEventListener('click', async () => {
    showScreen('screen-jobs');
    await loadJobs();
  });
  btnJobsNew.addEventListener('click', () => showScreen('screen-form'));
  btnNewVideo.addEventListener('click', () => showScreen('screen-form'));

  // ------------------------------------------------------------- Admin ----

  function fmtDate(iso) {
    // Correct only because the API returns ISO-8601 with a trailing Z.
    return iso ? new Date(iso).toLocaleString('pl-PL') : '-';
  }

  /** Append a <td>; `content` is a Node or plain text (never HTML). */
  function cell(tr, content, className) {
    const td = document.createElement('td');
    if (content instanceof Node) td.appendChild(content);
    else td.textContent = content == null ? '' : String(content);
    if (className) td.className = className;
    tr.appendChild(td);
    return td;
  }

  function headerRow(table, labels) {
    const tr = document.createElement('tr');
    labels.forEach(l => {
      const th = document.createElement('th');
      th.textContent = l;
      tr.appendChild(th);
    });
    table.appendChild(tr);
  }

  async function adminGet(url) {
    const res = await apiFetch(url);
    if (!res.ok) throw new Error(await errText(res));
    return res.json();
  }

  async function loadAdminJobs() {
    adminJobs.innerHTML = '';
    adminJobsEmpty.classList.add('hidden');
    const status = adminStatusFilter.value;
    let data;
    try {
      data = await adminGet('/admin/jobs' + (status ? `?status=${encodeURIComponent(status)}` : ''));
    } catch (e) {
      if (e.message !== 'UNAUTHORIZED') alert('Blad: ' + e.message);
      return;
    }
    if (!data.jobs.length) {
      adminJobsEmpty.classList.remove('hidden');
      return;
    }
    headerRow(adminJobs, ['Data', 'Uzytkownik', 'Status', 'Opis', 'Model', 'Koszt', 'Sceny', 'Blad', '']);
    data.jobs.forEach(job => adminJobs.appendChild(renderAdminJobRow(job)));
  }

  function renderAdminJobRow(job) {
    const tr = document.createElement('tr');
    cell(tr, fmtDate(job.created_at), 'nowrap');
    // user_id NULL = a job from before accounts existed (pre-0.2.0).
    cell(tr, job.user_email || '(bez konta)');

    const badge = document.createElement('span');
    badge.className = 'status-badge ' + statusClass(job.status);
    badge.textContent = STATUS_LABELS[job.status] || job.status;
    cell(tr, badge);

    cell(tr, job.prompt, 'clip').title = job.prompt || '';
    cell(tr, job.model);
    cell(tr, job.est_cost_usd != null ? '$' + Number(job.est_cost_usd).toFixed(2) : '-', 'nowrap');
    cell(tr, job.scene_count ? `${job.scene_count} / ${fmtDuration(job.total_duration_s || 0)}` : '-', 'nowrap');
    cell(tr, job.error || '', 'clip error-text').title = job.error || '';

    const actions = cell(tr, '');
    // Same rule as "Moje filmy": a live task owns these files, DELETE would 409.
    if (!BUSY_STATUSES.includes(job.status)) {
      const del = document.createElement('button');
      del.className = 'btn-link danger';
      del.textContent = 'Usun';
      del.onclick = () => adminDeleteJob(job, tr);
      actions.appendChild(del);
    }
    return tr;
  }

  async function adminDeleteJob(job, tr) {
    const owner = job.user_email || 'bez konta';
    if (!confirm(`Usunac job ${job.id} (${owner}) wraz z gotowym filmem? Tej operacji nie da sie cofnac.`)) return;
    try {
      const res = await apiFetch(`/admin/jobs/${job.id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(await errText(res));
    } catch (e) {
      if (e.message !== 'UNAUTHORIZED') alert('Blad: ' + e.message);
      return;
    }
    tr.remove();
    if (jobId === job.id) jobId = null;
    // Only the header row left.
    if (adminJobs.rows.length <= 1) {
      adminJobs.innerHTML = '';
      adminJobsEmpty.classList.remove('hidden');
    }
    // The job count and cost per user just changed.
    loadAdminUsers();
  }

  async function adminGrantCredits(u, btn) {
    const raw = prompt(
      `Ile kredytow dodac do konta ${u.email}?\n` +
      `Saldo teraz: ${u.credit_balance || 0} kr (100 kr = $1).\n` +
      `Liczba ujemna odejmuje - tak sie cofa pomylke.`,
      '500');
    if (raw === null) return;

    const amount = parseInt(raw, 10);
    // parseInt('abc') is NaN and parseInt('5x') is 5, hence checking both before
    // firing a request that changes someone's balance.
    if (!Number.isInteger(amount) || String(amount) !== raw.trim()) {
      alert('Podaj liczbe calkowita kredytow.');
      return;
    }
    if (amount === 0) return;

    const description = prompt('Powod (trafi do historii uzytkownika):',
                               'Korekta administratora');
    if (description === null) return;

    btn.disabled = true;
    try {
      const res = await apiFetch(`/admin/users/${u.id}/credits`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ amount, description: description || undefined }),
      });
      if (!res.ok) throw new Error(await errText(res));
      const data = await res.json();
      // Reload the whole table rather than patch one cell: a negative amount
      // moves the "Wydano" column too.
      await loadAdminUsers();
      // An admin can top up their own account, which the topbar must reflect.
      if (u.email === currentUser.email) setBalance(data.balance);
    } catch (e) {
      if (e.message !== 'UNAUTHORIZED') alert('Blad: ' + e.message);
      btn.disabled = false;
    }
  }

  async function loadAdminUsers() {
    adminUsers.innerHTML = '';
    let data;
    try {
      data = await adminGet('/admin/users');
    } catch (e) {
      if (e.message !== 'UNAUTHORIZED') alert('Blad: ' + e.message);
      return;
    }
    headerRow(adminUsers, ['Email', 'Rejestracja', 'Ostatnie logowanie', 'Joby',
                           'Koszt (szac.)', 'Wydano (kr)', 'Saldo', 'Konto', '']);
    data.users.forEach(u => adminUsers.appendChild(renderAdminUserRow(u)));
  }

  function renderAdminUserRow(u) {
    const tr = document.createElement('tr');
    const email = cell(tr, u.email);
    if (u.is_admin) {
      const tag = document.createElement('span');
      tag.className = 'admin-tag';
      tag.textContent = 'admin';
      email.appendChild(tag);
    }
    cell(tr, fmtDate(u.created_at), 'nowrap');
    cell(tr, fmtDate(u.last_login_at), 'nowrap');
    cell(tr, u.job_count);
    // Two numbers side by side on purpose: est_cost_usd is the planner's
    // prediction, credits_spent is what the ledger actually took. They diverge
    // exactly where the estimate was wrong — a failed scene, a retry, a job
    // abandoned after planning.
    cell(tr, '$' + Number(u.est_cost_usd || 0).toFixed(2), 'nowrap');
    cell(tr, u.credits_spent || 0, 'nowrap');
    cell(tr, u.credit_balance || 0, 'nowrap amount-plus');

    const state = document.createElement('span');
    state.className = 'status-badge ' + (u.is_active ? 'done' : 'error');
    state.textContent = u.is_active ? 'Aktywne' : 'Zablokowane';
    cell(tr, state);

    const actions = cell(tr, '');

    // The only top-up path with STRIPE_ENABLED=false. Offered on one's own
    // account too, unlike blocking, which an admin could not undo.
    const topup = document.createElement('button');
    topup.className = 'btn-link';
    topup.textContent = 'Kredyty';
    topup.onclick = () => adminGrantCredits(u, topup);
    actions.appendChild(topup);

    // /auth/me carries no id, so "is this me" goes by email. The server
    // refuses self-blocking with a 409 either way.
    if (u.email !== currentUser.email) {
      const btn = document.createElement('button');
      btn.className = 'btn-link' + (u.is_active ? ' danger' : '');
      btn.textContent = u.is_active ? 'Zablokuj' : 'Odblokuj';
      btn.onclick = () => adminSetActive(u, !u.is_active, btn);
      actions.appendChild(btn);
    }
    return tr;
  }

  async function adminSetActive(u, active, btn) {
    if (!active && !confirm(`Zablokowac ${u.email}? Konto zostanie wylogowane przy nastepnym zapytaniu; trwajace joby sie dokoncza.`)) return;
    btn.disabled = true;
    try {
      const res = await apiFetch(`/admin/users/${u.id}/active`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: active }),
      });
      if (!res.ok) throw new Error(await errText(res));
    } catch (e) {
      if (e.message !== 'UNAUTHORIZED') alert('Blad: ' + e.message);
      btn.disabled = false;
      return;
    }
    loadAdminUsers();
  }

  Object.entries(STATUS_LABELS).forEach(([value, label]) => {
    const opt = document.createElement('option');
    opt.value = value;
    opt.textContent = label;
    adminStatusFilter.appendChild(opt);
  });
  adminStatusFilter.addEventListener('change', loadAdminJobs);

  // ------------------------------------------------------------ Credits ----

  const CREDIT_TYPE_LABELS = {
    purchase: 'Zakup', usage: 'Zuzycie', refund: 'Zwrot', bonus: 'Bonus',
  };

  async function refreshBalance() {
    try {
      const res = await apiFetch('/auth/me');
      if (res.ok) setBalance((await res.json()).credit_balance);
    } catch (_) {}
  }

  function renderPackages(packages, stripeEnabled) {
    packagesList.innerHTML = '';
    packagesOff.classList.toggle('hidden', stripeEnabled);
    if (!stripeEnabled) return;

    packages.forEach(pkg => {
      const card = document.createElement('div');
      card.className = 'package-card';

      const label = document.createElement('div');
      label.className = 'package-label';
      label.textContent = pkg.label;
      card.appendChild(label);

      const credits = document.createElement('div');
      credits.className = 'package-credits';
      credits.textContent = pkg.credits + ' kredytow';
      card.appendChild(credits);

      const price = document.createElement('div');
      price.className = 'package-price';
      price.textContent = '$' + pkg.price_usd;
      card.appendChild(price);

      const btn = document.createElement('button');
      btn.className = 'btn-success';
      btn.textContent = 'Kup';
      btn.addEventListener('click', () => buyPackage(pkg.id, btn));
      card.appendChild(btn);

      packagesList.appendChild(card);
    });
  }

  async function buyPackage(packageId, btn) {
    btn.disabled = true;
    btn.textContent = 'Przekierowanie...';
    try {
      const res = await apiFetch('/credits/checkout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ package_id: packageId }),
      });
      if (!res.ok) throw new Error(await errText(res));
      // Payment finishes on Stripe's page; the webhook grants the credits, not
      // the user's return — closing the tab after paying loses nothing.
      location.href = (await res.json()).url;
    } catch (e) {
      if (e.message !== 'UNAUTHORIZED') alert(e.message);
      btn.disabled = false;
      btn.textContent = 'Kup';
    }
  }

  function renderCreditsHistory(entries) {
    creditsHistory.innerHTML = '';
    creditsHistoryEmpty.classList.toggle('hidden', entries.length > 0);
    if (!entries.length) return;

    headerRow(creditsHistory, ['Data', 'Typ', 'Zmiana', 'Saldo po', 'Opis']);
    entries.forEach(e => {
      const tr = document.createElement('tr');
      cell(tr, fmtDate(e.created_at), 'nowrap');
      cell(tr, CREDIT_TYPE_LABELS[e.type] || e.type, 'nowrap');
      cell(tr, (e.amount > 0 ? '+' : '') + e.amount, e.amount > 0 ? 'nowrap amount-plus' : 'nowrap amount-minus');
      cell(tr, e.balance_after, 'nowrap');
      cell(tr, e.description || '-');
      creditsHistory.appendChild(tr);
    });
  }

  // The credits screen is reached from the topbar (anywhere) or from "Doladuj"
  // on the plan, so "Wstecz" cannot lead to one fixed place — from "Moje filmy"
  // it would otherwise return to an empty plan.
  let screenBeforeCredits = 'screen-form';

  async function loadCredits() {
    const current = SCREEN_IDS.find(s => !document.getElementById(s).classList.contains('hidden'));
    if (current && current !== 'screen-credits') screenBeforeCredits = current;
    showScreen('screen-credits');
    try {
      const [pkgs, data] = await Promise.all([
        adminGet('/credits/packages'),
        adminGet('/credits'),
      ]);
      setBalance(data.balance);
      renderPackages(pkgs.packages, pkgs.stripe_enabled);
      renderCreditsHistory(data.history);
    } catch (e) {
      if (e.message !== 'UNAUTHORIZED') creditsHistoryEmpty.textContent = e.message;
    }
  }

  btnCredits.addEventListener('click', loadCredits);
  btnTopup.addEventListener('click', loadCredits);
  btnCreditsBack.addEventListener('click', () => showScreen(screenBeforeCredits));

  btnAdmin.addEventListener('click', () => {
    showScreen('screen-admin');
    loadAdminJobs();
    loadAdminUsers();
  });

  // ---------------------------------------------------------------- Boot ----

  (async function boot() {
    const params = new URLSearchParams(location.search);
    if (params.get('auth_error')) {
      loginError.textContent = 'Link wygasl lub zostal juz uzyty. Zaloguj sie ponownie.';
      loginError.classList.remove('hidden');
      history.replaceState({}, '', location.pathname);  // don't re-show on refresh
    }

    // Raw fetch, not apiFetch: a 401 is the expected logged-out case here, and
    // apiFetch would recurse into onLoggedOut().
    try {
      const res = await fetch('/auth/me');
      if (res.ok) {
        currentUser = await res.json();
        userEmail.textContent = currentUser.email;
        setBalance(currentUser.credit_balance);

        // Back from Checkout. The webhook that grants the credits can arrive
        // after this redirect, so the credits screen is more honest than a
        // "topped up" message: it shows the actual state, whatever it is.
        const paid = params.get('credits');
        history.replaceState({}, '', location.pathname);
        if (paid) { await loadCredits(); return; }

        showScreen('screen-form');
        return;
      }
    } catch (_) {}
    showScreen('screen-login');
  })();
})();
