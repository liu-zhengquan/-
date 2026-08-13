const { fetchFundEstimate } = require('../../utils/fund');

const STORAGE_KEY = 'tracked-funds';
const REFRESH_MS = 15000;

let timer = null;

Page({
  data: {
    fundCodeInput: '',
    funds: []
  },

  onLoad() {
    const storedCodes = wx.getStorageSync(STORAGE_KEY) || ['161725', '110022'];
    const validCodes = Array.isArray(storedCodes)
      ? storedCodes.filter((code) => /^\d{6}$/.test(code))
      : [];

    const funds = validCodes.map((code) => ({ code }));
    this.setData({ funds });

    this.refreshAll();
    timer = setInterval(() => this.refreshAll(), REFRESH_MS);
  },

  onUnload() {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
  },

  onInputChange(event) {
    this.setData({ fundCodeInput: event.detail.value || '' });
  },

  onAddFund() {
    const code = String(this.data.fundCodeInput).trim();
    if (!/^\d{6}$/.test(code)) {
      wx.showToast({ title: '请输入6位基金代码', icon: 'none' });
      return;
    }

    if (this.data.funds.some((item) => item.code === code)) {
      wx.showToast({ title: '该基金已存在', icon: 'none' });
      return;
    }

    const funds = [...this.data.funds, { code }];
    this.setData({ funds, fundCodeInput: '' });
    this.persistCodes(funds);
    this.refreshFund(code);
  },

  onRemoveFund(event) {
    const code = event.currentTarget.dataset.code;
    const funds = this.data.funds.filter((item) => item.code !== code);
    this.setData({ funds });
    this.persistCodes(funds);
  },

  onRefreshNow() {
    this.refreshAll();
    wx.showToast({ title: '已刷新', icon: 'none' });
  },

  persistCodes(funds) {
    const codes = funds.map((item) => item.code);
    wx.setStorageSync(STORAGE_KEY, codes);
  },

  refreshAll() {
    this.data.funds.forEach((item) => this.refreshFund(item.code));
  },

  refreshFund(code) {
    fetchFundEstimate(code)
      .then((payload) => {
        this.patchFund(code, {
          ...payload,
          code,
          error: ''
        });
      })
      .catch((error) => {
        this.patchFund(code, {
          code,
          error: error.message || '获取失败'
        });
      });
  },

  patchFund(code, patch) {
    const funds = this.data.funds.map((item) => {
      if (item.code !== code) {
        return item;
      }
      return { ...item, ...patch };
    });
    this.setData({ funds });
  }
});
