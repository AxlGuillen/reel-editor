// ReelForge — editor de segmentos de subtítulos, COMPARTIDO entre los módulos
// subtitles y reel_express. Mantiene un único flujo de edición en los dos.
//
// Se define como global (no IIFE), igual que los helpers de shared.js, para que
// los módulos lo usen. Opera sobre un array de segmentos { start, end, text, words }.
const SubtitleEditor = {
  fmtTime(s) {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${String(sec).padStart(2, "0")}`;
  },

  // Ajusta el alto del textarea a su contenido (wrap sin scroll horizontal).
  _autoGrow(ta) {
    ta.style.height = "auto";
    ta.style.height = `${ta.scrollHeight}px`;
  },

  // Renderiza la lista editable dentro de `container`. Edita seg.text in-place.
  render(container, segments) {
    container.innerHTML = "";
    segments.forEach((seg, i) => {
      const row = document.createElement("div");
      row.className = "sub-seg";
      const ts = `${this.fmtTime(seg.start)} → ${this.fmtTime(seg.end)}`;
      row.innerHTML = `
        <div class="sub-seg-meta"><span class="sub-seg-num">${i + 1}</span>
          <span class="sub-seg-time">${ts}</span></div>
        <textarea class="sub-seg-input text-input" rows="1"></textarea>`;
      const input = row.querySelector("textarea");
      input.value = seg.text;
      input.addEventListener("input", () => {
        seg.text = input.value;
        this._autoGrow(input);
      });
      container.appendChild(row);
      this._autoGrow(input);
    });
  },

  _originalText(seg) {
    return (seg.words || []).map((w) => w.word).join(" ").trim();
  },

  // Arma el payload para el render: si el texto cambió, NO manda words (el
  // backend redistribuye los tiempos); si quedó igual, manda los originales.
  collect(segments) {
    return segments.map((seg) => {
      const edited = seg.text.trim() !== this._originalText(seg);
      return {
        start: seg.start,
        end: seg.end,
        text: seg.text,
        words: edited ? null : seg.words,
      };
    });
  },
};
