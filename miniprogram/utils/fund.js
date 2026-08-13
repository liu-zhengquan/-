const ESTIMATE_URL = 'https://fundgz.1234567.com.cn/js/';

function request({ url, timeout = 8000 }) {
  return new Promise((resolve, reject) => {
    wx.request({
      url,
      method: 'GET',
      timeout,
      success: (res) => resolve(res),
      fail: (err) => reject(err)
    });
  });
}

function parseFundJs(jsText) {
  const raw = String(jsText || '').trim();
  if (!raw) {
    throw new Error('接口无返回内容');
  }

  const match = raw.match(/jsonpgz\((.*)\);?/);
  if (!match || !match[1]) {
    throw new Error('返回格式异常');
  }

  return JSON.parse(match[1]);
}

function fetchFundEstimate(code) {
  return request({ url: `${ESTIMATE_URL}${code}.js?rt=${Date.now()}` }).then((res) => {
    if (res.statusCode !== 200) {
      throw new Error(`接口错误(${res.statusCode})`);
    }
    return parseFundJs(res.data);
  });
}

module.exports = {
  fetchFundEstimate
};
