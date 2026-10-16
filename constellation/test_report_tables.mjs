import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const page = readFileSync(new URL("./index.html", import.meta.url), "utf8");
const script = [...page.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1];
const renderer = script.slice(script.indexOf("function escapeHtml("), script.indexOf("function closeReport("));
const { columnKind, renderMarkdown } = vm.runInNewContext(`${renderer}\n({ columnKind, renderMarkdown })`, { URL });

const dates = ["**2025 年 4 月 30 日**", "2025 年", "**2025 年 12 月 10–11 日**"];
const caseReference = "*Apple Inc. v. Epic Games, Inc.*, No. **25-1311**，2026 年开庭期";

test("a date column containing a case description uses a wrapping text column", () => {
  assert.equal(columnKind("日期", [...dates, caseReference]), "text");
  assert.equal(columnKind("日期", dates), "date");
});

test("the report renderer does not right-align or force date treatment on a mixed date column", () => {
  const report = `| 阶段 | 日期 | 结果 |
|---|---|---|
| 地区法院裁决 | ${dates[0]} | 法院认定违反禁令，案件移交联邦检察官调查。 |
| 佣金 | ${dates[1]} | 法院认定违反禁令。 |
| 上诉 | ${dates[2]} | 部分推翻救济令并发回重审。 |
| 案件编号 | ${caseReference} | — |`;
  const html = renderMarkdown(report);
  assert.match(html, /<th class="text">日期<\/th>/);
  const rows = [...html.matchAll(/<tbody>([\s\S]*?)<\/tbody>/g)][0][1];
  for (const row of rows.matchAll(/<tr>([\s\S]*?)<\/tr>/g)) {
    const cells = [...row[1].matchAll(/<td class="([^"]+)"[^>]*>/g)];
    assert.equal(cells[1][1], "text");
  }
});

test("numeric financial columns keep their existing number alignment", () => {
  const html = renderMarkdown(`| 项目 | 金额 |
|---|---|
| 营收 | 1,094,170,000 |
| 利润 | 297,890,000 |`);
  assert.match(html, /<th class="num"[^>]*>金额<\/th>/);
  assert.match(html, /<td class="num"[^>]*>1,094,170,000<\/td>/);
});

const ipoTable = `| 项目 | 数字 | 来源 |
|---|---|---|
| 开盘价 | **100.01 美元**（较 85 美元发行价高 15.01 美元） | NBC News |
| 首日收盘价 | **100.34 美元**（当日上涨约 18.04–18.05%） | CNBC / Investopedia |
| IPO 时市值 | **约 230 亿美元** | CNBC / Forbes / Statista |
| 原始价格区间（修订前） | 每股 108–135 美元 | CBS News |`;

function tableLayout(html){
  const table = /<table style="min-width:(\d+)px">([\s\S]*?)<\/table>/.exec(html);
  assert.ok(table, "the rendered table needs a content-based minimum width");
  const columns = [...table[2].matchAll(/<col style="width:([\d.]+)%">/g)].map(match => Number(match[1]));
  assert.ok(columns.length, "columns need explicit width allocation");
  assert.ok(Math.abs(columns.reduce((sum,width) => sum + width,0) - 100) < .01);
  return {minimum:Number(table[1]),columns};
}

test("the IPO table keeps the source column readable at desktop and narrow viewport widths", () => {
  const layout = tableLayout(renderMarkdown(ipoTable));
  assert.equal(layout.columns.length,3);
  for(const available of [280,520,900]){
    const width = Math.max(available,layout.minimum);
    assert.ok(width * layout.columns[2] / 100 >= 159, "source names must not be compressed into a vertical strip");
    assert.ok(width * layout.columns[1] / 100 >= 179, "the explanatory value column needs reading space");
  }
});

test("wide comparison tables retain readable columns through horizontal scrolling", () => {
  const headers = Array.from({length:8},(_,index) => `比较项 ${index + 1}`);
  const table = `| ${headers.join(" | ")} |\n| ${headers.map(() => "---").join(" | ")} |\n| ${headers.map(() => "包含多个条件与解释的完整研究结论，需要保留足够的阅读宽度").join(" | ")} |`;
  const layout = tableLayout(renderMarkdown(table));
  assert.equal(layout.columns.length,8);
  assert.ok(layout.minimum > 900, "a dense table should scroll instead of crushing all columns");
  for(const share of layout.columns) assert.ok(layout.minimum * share / 100 >= 179);
});

test("short two-column tables do not need an arbitrary desktop-sized minimum", () => {
  const layout = tableLayout(renderMarkdown("| 项目 | 状态 |\n|---|---|\n| 任务甲 | 完成 |"));
  assert.ok(layout.minimum <= 320);
});

test("short numeric values and explicit Markdown alignment retain their alignment", () => {
  const html = renderMarkdown("| 项目 | 数量 | 备注 |\n|---|---:|:---:|\n| 甲 | 12 | 正常 |\n| 乙 | 34 | 待核查 |");
  assert.match(html, /<td class="num" style="text-align:right">12<\/td>/);
  assert.match(html, /<td class="label" style="text-align:center">正常<\/td>/);
});

test("uneven rows keep the declared column count and retain overflow text", () => {
  const html = renderMarkdown("| 项目 | 内容 |\n|---|---|\n| 甲 | 第一段 | 第二段 |\n| 乙 |");
  const body = /<tbody>([\s\S]*?)<\/tbody>/.exec(html)[1];
  for(const row of body.matchAll(/<tr>([\s\S]*?)<\/tr>/g)) assert.equal([...row[1].matchAll(/<td /g)].length,2);
  assert.ok(html.includes("第一段 | 第二段"));
});
