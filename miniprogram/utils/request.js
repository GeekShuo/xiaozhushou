// 与后端 HTTP API 通信。后端接口:POST {baseUrl}/api  body={name, args}
function call(name, ...args) {
  const app = getApp();
  return new Promise((resolve, reject) => {
    wx.request({
      url: app.globalData.baseUrl + "/api",
      method: "POST",
      header: { "Content-Type": "application/json" },
      data: { name, args },
      success: (res) => {
        const d = res.data || {};
        if (d.error) reject(new Error(d.error));
        else resolve(d.result);
      },
      fail: (e) => reject(e)
    });
  });
}

module.exports = { call };
