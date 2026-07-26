const { call } = require("../../utils/request");

Page({
  data: { goals: [], text: "", persona: "" },
  onShow() { this.load(); },
  async load() {
    const st = await call("get_state");
    this.setData({ goals: st.goals || [], persona: st.persona_id });
  },
  onInput(e) { this.setData({ text: e.detail.value }); },
  async onNew() {
    if (!this.data.text.trim()) return;
    await call("start_goal", this.data.text);
    this.setData({ text: "" });
    wx.switchTab({ url: "/pages/chat/chat" });
  },
  async onSwitch(e) {
    const id = e.currentTarget.dataset.id;
    await call("switch_goal", id);
    wx.switchTab({ url: "/pages/chat/chat" });
  }
});
