// Lay out the book in fixed pages, then save each page as a picture and every word's box and text.
// usage: node layout-pages.mjs <chrome> <build dir>
import { chromium } from "playwright-core";
import { readFileSync, writeFileSync } from "node:fs";

const [executablePath, dir] = process.argv.slice(2);
const book = JSON.parse(readFileSync(`${dir}/book.json`, "utf8"));
const browser = await chromium.launch({ executablePath });
const page = await browser.newPage({ viewport: { width: 520, height: 900 }, deviceScaleFactor: 3 });
await page.goto(`file://${dir}/layout.html`);
await page.evaluate(() => document.fonts.load('400 14px "Noto Serif Bengali"', "অ"));
await page.evaluate(() => document.fonts.load('700 14px "Noto Serif Bengali"', "অ"));

const count = await page.evaluate((book) => {
  const words = (text) => text.split(/\s+/).filter(Boolean).map((w) => `<span class="w">${w.replace(/&/g, "&amp;").replace(/</g, "&lt;")}</span>`).join(" ");
  const newPage = () => {
    const sheet = document.createElement("div");
    sheet.className = "page";
    const content = document.createElement("div");
    content.className = "content";
    sheet.append(content);
    document.body.append(sheet);
    return content;
  };
  const fits = (content) => content.scrollHeight <= content.clientHeight + 0.5;

  let content = newPage();
  content.innerHTML = `<div class="title-page"><h1>${words(book.title)}</h1><p class="author">${words(book.author)}</p><p class="note">${words(book.note)}</p></div>`;
  for (const chapter of book.chapters) {
    content = newPage();
    content.insertAdjacentHTML("beforeend", `<h2>${words(chapter.title)}</h2>`);
    for (const text of chapter.paragraphs) {
      let tokens = text.split(/\s+/).filter(Boolean);
      let continued = false;
      while (tokens.length) {
        const p = document.createElement("p");
        if (continued) p.className = "cont";
        content.append(p);
        p.innerHTML = words(tokens.join(" "));
        if (fits(content)) break;
        // Find how many words fit on this page, then carry the rest over.
        let low = 0, high = tokens.length;
        while (low < high) {
          const mid = Math.ceil((low + high) / 2);
          p.innerHTML = words(tokens.slice(0, mid).join(" "));
          if (fits(content)) low = mid; else high = mid - 1;
        }
        if (low === 0) p.remove(); else p.innerHTML = words(tokens.slice(0, low).join(" "));
        if (low > 0) p.style.textAlignLast = "justify"; // a paragraph broken by the page stays justified
        tokens = tokens.slice(low);
        continued = low > 0;
        content = newPage();
      }
    }
  }
  return document.querySelectorAll(".page").length;
}, book);

const pages = [];
const sheets = await page.$$(".page");
for (const [index, sheet] of sheets.entries()) {
  await sheet.screenshot({ path: `${dir}/page-${String(index + 1).padStart(2, "0")}.png` });
  pages.push(await sheet.evaluate((el) => {
    const origin = el.getBoundingClientRect();
    return [...el.querySelectorAll(".w")].map((w) => {
      const r = w.getBoundingClientRect();
      const size = parseFloat(getComputedStyle(w).fontSize);
      return { text: w.textContent, x: r.left - origin.left, y: r.top - origin.top, w: r.width, h: r.height, size };
    });
  }));
}
writeFileSync(`${dir}/words.json`, JSON.stringify({ width: 487, height: 765, pages }));
console.log(`${count} text pages laid out;`, pages.map((p) => p.length).join(", "), "words");
await browser.close();
