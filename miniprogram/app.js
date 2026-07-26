// 小程序入口。后端地址改成你部署的 HTTP 服务(python src/main.py --web 启动的地址)。
App({
  globalData: {
    // 部署到服务器后改成 https://你的域名 ,本地调试用电脑局域网 IP
    baseUrl: "http://127.0.0.1:8080",
    openid: ""
  },
  onLaunch() {
    // 真实环境应通过 wx.login 拿 code 换 openid,用于微信好友/订阅消息推送
  }
});
