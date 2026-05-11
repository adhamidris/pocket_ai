const markedUrl = 'https://cdn.jsdelivr.net/npm/marked/marked.min.js';
(async () => {
  const res = await fetch(markedUrl);
  const code = await res.text();
  const script = new (require('vm').Script)(code + '; global.marked = marked;');
  script.runInThisContext();

  const md = `| Header 1 | Header 2 |
|---|---|
C`;

  console.log(global.marked.parse(md));
})();