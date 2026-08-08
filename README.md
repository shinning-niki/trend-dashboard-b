# 趋势看板 B · A股自选

基于趋势动物 API 的个人趋势交易看板（第二个看板，纪律 B1.0）。

- **更新频率**：每日 16:00（北京时间），GitHub Actions 自动运行
- **数据日期保障**：A股数据约 16:24-16:30 更新，脚本在 15:50-16:40 窗口内每 5 分钟免费重查 `getUpdateStatus`，确保取到当日数据
- **持仓**：10 只 A股/组合（隆22转债接口未收录，看板标注数据缺失）
- **候选源**：温转热(A股)、右侧个股(A股)、近期历史新高(A股) 三榜每日穿透
- **仓位规则**：危险信号/温转平→清仓；温转热·热→全仓；温转热·沸→半仓止盈
- **推荐筛选**：九条件（温转热·热、强度>95、行业温度≥温、市值>100亿、日成交额>2亿、股价≤500、节气清明-夏至、非ST、非北交所）

## 运行方式

API Key 通过 GitHub Secret `TREND_API_KEY` 注入，**不得写入仓库**。

本地运行：

```bash
export TREND_API_KEY=<your_key>
python dashboard/fetch_dashboard_data.py
python dashboard/build_dashboard.py
# 输出 dashboard/dist/index.html
```

## 数据来源声明

数据与指标来自[趋势动物 API](https://www.trendtrader.cn/)，仅供趋势交易研究与纪律执行参考，不构成投资建议或收益承诺。市场有风险，盈亏自负。
