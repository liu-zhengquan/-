# 基金实时估值助手（微信小程序版）

本项目已调整为 **微信小程序版本**，支持输入基金代码并实时查看估值。

## 功能

- 添加/移除基金代码（6 位数字）
- 默认预置两只示例基金（161725、110022）
- 每 15 秒自动刷新，可手动立即刷新
- 本地缓存监控列表（`wx.setStorageSync`）

## 目录结构

```text
miniprogram/
  app.js
  app.json
  app.wxss
  sitemap.json
  pages/index/
    index.js
    index.json
    index.wxml
    index.wxss
  utils/fund.js
```

## 怎么使用（微信开发者工具）

1. 打开微信开发者工具，选择“导入项目”。
2. 项目目录选择仓库根目录 `/workspace/-`。
3. 将 **小程序源码目录** 设置为 `miniprogram`。
4. 填写你自己的 AppID（或选择测试号）。
5. 进入后即可预览首页，输入 6 位基金代码并点击“添加”。

## 接口说明

- 当前通过 `https://fundgz.1234567.com.cn/js/{code}.js` 获取估值。
- 小程序端使用 `wx.request` 拉取后解析 `jsonpgz(...)` 返回体。
- 真机调试前，请在微信公众平台把 `https://fundgz.1234567.com.cn` 加入 request 合法域名。

## 说明

仓库里保留了上一版 Web 原型文件（`index.html` / `app.js` / `styles.css`），
本次交付以 `miniprogram/` 下的微信小程序实现为准。
