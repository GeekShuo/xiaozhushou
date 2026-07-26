const { call } = require("../../utils/request");

Page({
  data: { year: 2026, month: 1, days: {}, today: "", cells: [] },
  onLoad() {
    const d = new Date();
    this.setData({ year: d.getFullYear(), month: d.getMonth() + 1 });
    this.load();
  },
  async load() {
    const r = await call("get_calendar", this.data.year, this.data.month);
    // 构造 42 格日历(周一起)
    const first = new Date(this.data.year, this.data.month - 1, 1);
    let lead = (first.getDay() + 6) % 7;
    const total = new Date(this.data.year, this.data.month, 0).getDate();
    const cells = [];
    for (let i = 0; i < lead; i++) cells.push({ empty: true });
    for (let d = 1; d <= total; d++) {
      const iso = `${this.data.year}-${String(this.data.month).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
      cells.push({ empty: false, day: d, iso, count: (r.days[iso] || []).length, today: iso === r.today });
    }
    this.setData({ days: r.days, today: r.today, cells });
  },
  prev() { let { year, month } = this.data; month--; if (month < 1) { month = 12; year--; } this.setData({ year, month }); this.load(); },
  next() { let { year, month } = this.data; month++; if (month > 12) { month = 1; year++; } this.setData({ year, month }); this.load(); }
});
