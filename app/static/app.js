(() => {
  // State
  let files = [];
  let jobId = null;
  let eventSource = null;
  let currentScenes = [];
  let currentUser = null;
  // How many source images the job has, per the API: `files` is empty after a
  // resume, so the image picker has to count from the server instead.
  let imageCount = 0;

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

  // Screens are mutually exclusive; showScreen is the only thing that toggles them.
  const SCREEN_IDS = ['screen-login', 'screen-form', 'screen-plan', 'screen-progress', 'screen-jobs'];

  function showScreen(id) {
    SCREEN_IDS.forEach(s => {
      document.getElementById(s).classList.toggle('hidden', s !== id);
    });
    userBar.classList.toggle('hidden', !currentUser);
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

  function onLoggedOut() {
    currentUser = null;
    if (eventSource) { eventSource.close(); eventSource = null; }
    // Must clear staged work too, or the next user on this browser inherits the
    // previous user's uploads and job id.
    jobId = null;
    files = [];
    currentScenes = [];
    renderPreviews();
    updatePlanButton();
    showScreen('screen-login');
  }

  // Duration slider
  durationEl.addEventListener('input', () => {
    const v = parseInt(durationEl.value);
    const m = Math.floor(v / 60);
    const s = v % 60;
    durationLabel.textContent = m > 0 ? `${m}m ${s}s` : `${v}s`;
  });
  // Init label
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
    }
    renderPreviews();
    updatePlanButton();
  }

  function renderPreviews() {
    previews.innerHTML = '';
    files.forEach((f, i) => {
      const wrap = document.createElement('div');
      wrap.className = 'preview-item';
      const img = document.createElement('img');
      img.className = 'preview-thumb';
      img.src = URL.createObjectURL(f);
      const btn = document.createElement('button');
      btn.className = 'preview-remove';
      btn.textContent = '\u00d7';
      btn.onclick = () => { files.splice(i, 1); renderPreviews(); updatePlanButton(); };
      wrap.appendChild(img);
      wrap.appendChild(btn);
      previews.appendChild(wrap);
    });
  }

  function updatePlanButton() {
    btnPlan.disabled = files.length === 0 || promptEl.value.trim() === '';
  }
  promptEl.addEventListener('input', updatePlanButton);

  // Plan
  btnPlan.addEventListener('click', async () => {
    btnPlan.disabled = true;
    btnPlan.textContent = 'Planowanie...';

    const fd = new FormData();
    files.forEach(f => fd.append('images', f));
    fd.append('prompt', promptEl.value.trim());
    fd.append('model', modelEl.value);
    fd.append('target_duration_s', durationEl.value);

    try {
      const res = await apiFetch('/jobs', { method: 'POST', body: fd });
      if (!res.ok) throw new Error(await errText(res));
      const data = await res.json();
      jobId = data.job_id;

      connectSSE();
      const planRes = await apiFetch(`/jobs/${jobId}/plan`, { method: 'POST' });
      if (!planRes.ok) throw new Error(await errText(planRes));
    } catch (e) {
      if (e.message !== 'UNAUTHORIZED') {
        alert('Blad: ' + e.message);
        btnPlan.disabled = false;
        btnPlan.textContent = 'Zaplanuj';
      }
    }
  });

  function connectSSE() {
    if (eventSource) eventSource.close();
    eventSource = new EventSource(`/jobs/${jobId}/events`);

    eventSource.addEventListener('planned', e => {
      const data = JSON.parse(e.data);
      currentScenes = data.scenes;
      showPlanScreen(data.est_cost_usd);
    });

    eventSource.addEventListener('status', e => {
      const data = JSON.parse(e.data);
      statusText.textContent = translateStatus(data.status);
      updateProgressBar(data.status);
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

  // Cost per second by model
  const modelCosts = { 'wan': 0.05, 'kling-2.5-turbo': 0.07, 'veo-3.1-fast': 0.10 };

  function recalcCost() {
    const totalS = currentScenes.reduce((sum, s) => sum + s.duration_s, 0);
    const cps = modelCosts[modelEl.value] || 0.05;
    const cost = (totalS * cps).toFixed(2);
    costAmount.textContent = '$' + cost;
    const m = Math.floor(totalS / 60);
    const s = totalS % 60;
    totalDuration.textContent = `(${m}m ${s}s)`;
  }

  function showPlanScreen(cost) {
    showScreen('screen-plan');
    renderScenes();
    recalcCost();
    btnPlan.disabled = false;
    btnPlan.textContent = 'Zaplanuj';
    // showProgressScreen() disables it, and a resume can land here afterwards.
    btnGenerate.disabled = false;
  }

  /** Local File while the upload is still in this tab, server copy after a resume. */
  function sceneImageSrc(imageIndex) {
    if (files[imageIndex]) return URL.createObjectURL(files[imageIndex]);
    if (jobId) return `/jobs/${jobId}/images/${imageIndex}`;
    return '';
  }

  function renderScenes() {
    scenesList.innerHTML = '';

    currentScenes.forEach((s, i) => {
      const card = document.createElement('div');
      card.className = 'scene-card';

      const img = document.createElement('img');
      img.src = sceneImageSrc(s.image_index);

      const info = document.createElement('div');
      info.className = 'scene-info';

      // Header row with scene number and remove button
      const idx = document.createElement('div');
      idx.className = 'scene-idx';
      const label = document.createElement('span');
      label.textContent = `Scena ${i + 1}` + (s.chain_from_prev ? ' (kontynuacja)' : '');
      idx.appendChild(label);

      if (currentScenes.length > 1) {
        const removeBtn = document.createElement('button');
        removeBtn.className = 'scene-remove';
        removeBtn.textContent = 'Usun';
        removeBtn.onclick = () => removeScene(i);
        idx.appendChild(removeBtn);
      }

      // Prompt textarea
      const ta = document.createElement('textarea');
      ta.value = s.sub_prompt;
      ta.addEventListener('change', () => {
        currentScenes[i].sub_prompt = ta.value;
        syncScenesToBackend(i);
      });

      // Controls row: duration select + image select + chain checkbox
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
        currentScenes[i].duration_s = parseInt(durSel.value);
        recalcCost();
        syncScenesToBackend(i);
      });

      const imgLabel = document.createElement('label');
      imgLabel.textContent = 'Zdjecie:';
      const imgSel = document.createElement('select');
      for (let fi = 0; fi < (files.length || imageCount); fi++) {
        const opt = document.createElement('option');
        opt.value = fi; opt.textContent = `#${fi + 1}`;
        if (fi === s.image_index) opt.selected = true;
        imgSel.appendChild(opt);
      }
      imgSel.addEventListener('change', () => {
        currentScenes[i].image_index = parseInt(imgSel.value);
        img.src = sceneImageSrc(currentScenes[i].image_index);
        syncScenesToBackend(i);
      });

      controls.appendChild(durLabel);
      controls.appendChild(durSel);
      controls.appendChild(imgLabel);
      controls.appendChild(imgSel);

      info.appendChild(idx);
      info.appendChild(ta);
      info.appendChild(controls);
      card.appendChild(img);
      card.appendChild(info);
      scenesList.appendChild(card);
    });
  }

  function removeScene(idx) {
    currentScenes.splice(idx, 1);
    // Fix chain_from_prev for new first scene
    if (currentScenes.length > 0) {
      currentScenes[0].chain_from_prev = false;
    }
    renderScenes();
    recalcCost();
    syncAllScenes();
  }

  // Add scene button
  btnAddScene.addEventListener('click', () => {
    const newScene = {
      image_index: 0,
      sub_prompt: 'Nowa scena - opisz ruch kamery...',
      duration_s: 5,
      chain_from_prev: false,
    };
    currentScenes.push(newScene);
    renderScenes();
    recalcCost();
    syncAllScenes();
  });

  async function syncScenesToBackend(idx) {
    const s = currentScenes[idx];
    const fd = new FormData();
    fd.append('sub_prompt', s.sub_prompt);
    fd.append('duration_s', s.duration_s);
    fd.append('image_index', s.image_index);
    try {
      await apiFetch(`/jobs/${jobId}/scenes/${idx}/update`, { method: 'POST', body: fd });
    } catch (_) {}  // apiFetch has already switched screens on 401
  }

  async function syncAllScenes() {
    try {
      await apiFetch(`/jobs/${jobId}/scenes/sync`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scenes: currentScenes }),
      });
    } catch (_) {}
  }

  // Generate
  btnGenerate.addEventListener('click', async () => {
    // Sync all scenes before generating
    await syncAllScenes();

    showProgressScreen();

    try {
      const res = await apiFetch(`/jobs/${jobId}/generate`, { method: 'POST' });
      if (!res.ok) throw new Error(await errText(res));
    } catch (e) {
      if (e.message !== 'UNAUTHORIZED') showError(e.message);
    }
  });

  function showProgressScreen(text) {
    btnGenerate.disabled = true;
    showScreen('screen-progress');
    errorBox.classList.add('hidden');
    videoResult.classList.add('hidden');
    progressBar.style.width = '30%';
    statusText.textContent = text || 'Generowanie klipow...';
    statusText.className = 'status-badge generating';
  }

  // Back
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
      showPlanScreen(detail.est_cost_usd);
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
    prompt.textContent = job.prompt;

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

    if (RESUMABLE_STATUSES.includes(job.status)) {
      const resume = document.createElement('button');
      resume.className = 'btn-link';
      resume.textContent = 'Wznow';
      resume.onclick = () => resumeJob(job);
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
        showScreen('screen-form');
        return;
      }
    } catch (_) {}
    showScreen('screen-login');
  })();
})();
