(() => {
  // State
  let files = [];
  let jobId = null;
  let eventSource = null;
  let currentScenes = [];

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
      const res = await fetch('/jobs', { method: 'POST', body: fd });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      jobId = data.job_id;

      connectSSE();
      await fetch(`/jobs/${jobId}/plan`, { method: 'POST' });
    } catch (e) {
      alert('Blad: ' + e.message);
      btnPlan.disabled = false;
      btnPlan.textContent = 'Zaplanuj';
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

    eventSource.addEventListener('error', e => {
      try {
        const data = JSON.parse(e.data);
        showError(data.error);
      } catch (_) {}
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
    screenForm.classList.add('hidden');
    screenPlan.classList.remove('hidden');
    screenProgress.classList.add('hidden');
    renderScenes();
    recalcCost();
    btnPlan.disabled = false;
    btnPlan.textContent = 'Zaplanuj';
  }

  function renderScenes() {
    scenesList.innerHTML = '';

    currentScenes.forEach((s, i) => {
      const card = document.createElement('div');
      card.className = 'scene-card';

      const img = document.createElement('img');
      if (files[s.image_index]) {
        img.src = URL.createObjectURL(files[s.image_index]);
      }

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
      files.forEach((f, fi) => {
        const opt = document.createElement('option');
        opt.value = fi; opt.textContent = `#${fi + 1}`;
        if (fi === s.image_index) opt.selected = true;
        imgSel.appendChild(opt);
      });
      imgSel.addEventListener('change', () => {
        currentScenes[i].image_index = parseInt(imgSel.value);
        // Update thumbnail
        if (files[currentScenes[i].image_index]) {
          img.src = URL.createObjectURL(files[currentScenes[i].image_index]);
        }
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
    try {
      await fetch(`/jobs/${jobId}/scenes/${idx}/update`, { method: 'POST', body: fd });
    } catch (_) {}
  }

  async function syncAllScenes() {
    try {
      await fetch(`/jobs/${jobId}/scenes/sync`, {
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

    btnGenerate.disabled = true;
    screenPlan.classList.add('hidden');
    screenProgress.classList.remove('hidden');
    errorBox.classList.add('hidden');
    videoResult.classList.add('hidden');
    progressBar.style.width = '30%';
    statusText.textContent = 'Generowanie klipow...';
    statusText.className = 'status-badge generating';

    try {
      const res = await fetch(`/jobs/${jobId}/generate`, { method: 'POST' });
      if (!res.ok) throw new Error(await res.text());
    } catch (e) {
      showError(e.message);
    }
  });

  // Back
  btnBack.addEventListener('click', () => {
    screenPlan.classList.add('hidden');
    screenForm.classList.remove('hidden');
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
})();
