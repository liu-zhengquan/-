const STORAGE_KEY = 'tracked-funds';
const REFRESH_MS = 15_000;

const form = document.getElementById('fund-form');
const codeInput = document.getElementById('fund-code');
const listEl = document.getElementById('fund-list');
const refreshNowBtn = document.getElementById('refresh-now');
const template = document.getElementById('fund-item-template');

let trackedCodes = loadCodes();
let fundDataMap = new Map();

renderList();
refreshAll();
setInterval(refreshAll, REFRESH_MS);

form.addEventListener('submit', (event) => {
  event.preventDefault();
  const code = codeInput.value.trim();
  if (!/^\d{6}$/.test(code)) {
    alert('请输入 6 位数字基金代码。');
    return;
  }

  if (trackedCodes.includes(code)) {
    alert('该基金已在列表中。');
    return;
  }

  trackedCodes.push(code);
  saveCodes();
  codeInput.value = '';
  renderList();
  refreshFund(code);
});

refreshNowBtn.addEventListener('click', refreshAll);

function loadCodes() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return ['161725', '110022'];
    }
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((code) => /^\d{6}$/.test(code)) : [];
  } catch {
    return [];
  }
}

function saveCodes() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(trackedCodes));
}

function refreshAll() {
  trackedCodes.forEach((code) => refreshFund(code));
}

function refreshFund(code) {
  fetchFundEstimate(code)
    .then((data) => {
      fundDataMap.set(code, data);
      patchCard(code, data);
    })
    .catch((error) => {
      patchCard(code, { error: error.message || '获取失败' });
    });
}

function renderList() {
  listEl.innerHTML = '';

  if (trackedCodes.length === 0) {
    const li = document.createElement('li');
    li.textContent = '暂无监控基金，请先添加基金代码。';
    li.className = 'fund-item';
    listEl.appendChild(li);
    return;
  }

  trackedCodes.forEach((code) => {
    const node = template.content.firstElementChild.cloneNode(true);
    node.dataset.code = code;
    node.querySelector('.fund-code').textContent = code;

    node.querySelector('.remove-btn').addEventListener('click', () => {
      trackedCodes = trackedCodes.filter((value) => value !== code);
      fundDataMap.delete(code);
      saveCodes();
      renderList();
    });

    listEl.appendChild(node);
    const cached = fundDataMap.get(code);
    if (cached) {
      patchCard(code, cached);
    }
  });
}

function patchCard(code, data) {
  const card = listEl.querySelector(`.fund-item[data-code="${code}"]`);
  if (!card) {
    return;
  }

  if (data.error) {
    card.querySelector('.fund-name').textContent = '获取失败';
    card.querySelector('.gztime').textContent = data.error;
    return;
  }

  card.querySelector('.fund-name').textContent = data.name;
  card.querySelector('.dwjz').textContent = data.dwjz;
  card.querySelector('.gsz').textContent = data.gsz;

  const changeEl = card.querySelector('.gszzl');
  changeEl.textContent = `${data.gszzl}%`;
  changeEl.classList.remove('up', 'down');
  changeEl.classList.add(Number(data.gszzl) >= 0 ? 'up' : 'down');

  card.querySelector('.gztime').textContent = data.gztime;
}

function fetchFundEstimate(code) {
  return new Promise((resolve, reject) => {
    const callbackName = `fundEstimateCallback_${code}_${Date.now()}`;
    const script = document.createElement('script');
    const timer = setTimeout(() => cleanup(new Error('请求超时')), 8000);

    function cleanup(error, data) {
      clearTimeout(timer);
      script.remove();
      delete window[callbackName];
      if (error) {
        reject(error);
      } else {
        resolve(data);
      }
    }

    window[callbackName] = (payload) => {
      cleanup(null, payload);
    };

    script.onerror = () => cleanup(new Error('网络错误或接口不可用'));
    script.src = `https://fundgz.1234567.com.cn/js/${code}.js?rt=${Date.now()}&callback=${callbackName}`;

    document.body.appendChild(script);
  });
}
