const { call } = require("../../utils/request");

Page({
  data: { msgs: [], input: "", onboarding: false },
  onShow() { this.load(); },
  async load() {
    const st = await call("get_state");
    this.setData({ msgs: st.chat || [], onboarding: !!st.onboarding });
  },
  onInput(e) { this.setData({ input: e.detail.value }); },
  async onSend() {
    const v = this.data.input.trim();
    if (!v) return;
    if (this.data.onboarding) {
      const r = await call("goal_chat", v);
      if (r.onboarding_complete) { this.setData({ input: "" }); await this.load(); wx.showToast({ title: "计划已生成", icon: "success" }); return; }
      this.setData({ input: "" });
      await this.load();
    } else {
      const r = await call("chat", v, null);
      this.setData({ input: "" });
      await this.load();
    }
  }
});
