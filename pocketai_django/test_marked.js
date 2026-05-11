const markedUrl = 'https://cdn.jsdelivr.net/npm/marked/marked.min.js';
(async () => {
  const res = await fetch(markedUrl);
  const code = await res.text();
  const script = new (require('vm').Script)(code + '; global.marked = marked;');
  script.runInThisContext();

  function stripStandaloneMarkdownPipeArtifacts(text) {
    const raw = (text || "").toString();
    if (!raw) return "";

    const lines = raw.split("\n");
    const isDividerLine = (value) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/.test(value || "");
    const isPipeNoiseLine = (value) => {
      const trimmed = (value || "").trim();
      return trimmed === "|" || trimmed === "|:" || trimmed === ":|" || trimmed === "||" || trimmed === "||:";
    };
    const hasPipe = (value) => /\|/.test(value || "");

    const out = [];
    let inFence = false;
    for (let idx = 0; idx < lines.length; idx += 1) {
      let line = lines[idx];
      const trimmedStart = (line || "").trimStart();
      if (trimmedStart.startsWith("\`\`\`")) {
        inFence = !inFence;
        out.push(line);
        continue;
      }
      if (inFence) {
        out.push(line);
        continue;
      }

      const trimmed = (line || "").trim();
      if (!trimmed) {
        out.push(line);
        continue;
      }

      if (isPipeNoiseLine(trimmed)) {
        continue;
      }

      if (isDividerLine(trimmed)) {
        let prev = idx - 1;
        while (prev >= 0 && !(lines[prev] || "").trim()) prev -= 1;
        let next = idx + 1;
        while (next < lines.length && !(lines[next] || "").trim()) next += 1;
        const prevHasPipe = prev >= 0 && hasPipe(lines[prev]);
        const nextHasPipe = next < lines.length && hasPipe(lines[next]);
        if (!(prevHasPipe && nextHasPipe)) {
          continue;
        }
      }

      const pipeCount = ((line || "").match(/\|/g) || []).length;
      if (pipeCount === 1) {
        const startsWithPipe = /^\s*\|/.test(line || "");
        const endsWithPipe = /\|\s*$/.test(line || "");
        if (startsWithPipe || endsWithPipe) {
          let prev = idx - 1;
          while (prev >= 0 && !(lines[prev] || "").trim()) prev -= 1;
          let next = idx + 1;
          while (next < lines.length && !(lines[next] || "").trim()) next += 1;
          const prevHasPipe = prev >= 0 && hasPipe(lines[prev]);
          const nextHasPipe = next < lines.length && hasPipe(lines[next]);
          const tableContext = prevHasPipe && nextHasPipe;
          if (!tableContext) {
            line = (line || "").replace(/^\s*\|\s*/, "");
            line = line.replace(/\s*\|\s*$/, "");
            if (!line.trim()) continue;
          }
        }
      }

      out.push(line);
    }

    return out.join("\n");
  }

  const md3 = `| Header 1 | Header 2 |
|---|---|
| Cell 1`;
  const stripped = stripStandaloneMarkdownPipeArtifacts(md3);
  console.log("Stripped:", JSON.stringify(stripped));
  console.log("Parsed:", global.marked.parse(stripped));
})();