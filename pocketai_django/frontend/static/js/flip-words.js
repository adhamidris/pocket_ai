document.addEventListener("DOMContentLoaded", () => {
  const targetElement = document.querySelector(".flip-words-target");
  if (!targetElement) return;

  const documentDir = (document.documentElement.getAttribute("dir") || "ltr").toLowerCase();
  const isRtlDocument = documentDir === "rtl";
  const preferWholeWordAnimation = window.matchMedia("(max-width: 640px)").matches;

  const initialWord = (targetElement.textContent || "").trim();
  const wordsFromData = (targetElement.dataset.words || "")
    .split("|")
    .map((value) => value.trim())
    .filter(Boolean);

  const words = wordsFromData.length ? wordsFromData : (initialWord ? [initialWord] : []);
  if (!words.length) return;

  let currentWordIndex = 0;

  const wrapper = document.createElement("div");
  wrapper.className = "flip-words-wrapper";
  targetElement.textContent = "";
  targetElement.appendChild(wrapper);

  const containsArabic = (text) => /[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]/.test(text);

  const createWordSpan = (text) => {
    const wordSpan = document.createElement("span");
    wordSpan.className = "flip-word text-primary";
    const isArabicWord = containsArabic(text);

    // Keep bidi isolation per token to avoid reversed mixed-script rendering in RTL.
    wordSpan.setAttribute("dir", isArabicWord ? "rtl" : "ltr");
    wordSpan.style.unicodeBidi = "isolate";

    // Arabic shaping breaks when split into single letters; render as one token in RTL.
    const shouldSplitLetters = !preferWholeWordAnimation && !isRtlDocument && !isArabicWord;
    if (!shouldSplitLetters) {
      const token = document.createElement("span");
      token.textContent = text;
      token.className = "flip-letter flip-token";
      wordSpan.appendChild(token);
      return wordSpan;
    }

    Array.from(text).forEach((char) => {
      const letterSpan = document.createElement("span");
      letterSpan.textContent = char;
      letterSpan.className = "flip-letter";
      wordSpan.appendChild(letterSpan);
    });

    return wordSpan;
  };

  const animateIn = (wordSpan) => {
    const letters = wordSpan.querySelectorAll(".flip-letter");
    letters.forEach((letter, i) => {
      letter.style.animationDelay = `${i * 0.04}s`;
      letter.classList.add("active");
    });
  };

  const animateOut = (wordSpan) => {
    const letters = wordSpan.querySelectorAll(".flip-letter");
    letters.forEach((letter) => {
      letter.style.animationDelay = "0s";
    });

    setTimeout(() => {
      if (wordSpan.parentNode) {
        wordSpan.remove();
      }
    }, 250);
  };

  const cycleWords = () => {
    const currentWord = words[currentWordIndex];
    const newWordSpan = createWordSpan(currentWord);

    const existingWords = wrapper.querySelectorAll(".flip-word");
    existingWords.forEach((oldWordSpan) => {
      if (!oldWordSpan.classList.contains("exiting")) {
        oldWordSpan.classList.add("exiting");
        animateOut(oldWordSpan);
      }
    });

    wrapper.appendChild(newWordSpan);
    animateIn(newWordSpan);

    currentWordIndex = (currentWordIndex + 1) % words.length;
  };

  cycleWords();
  if (words.length > 1) {
    setInterval(cycleWords, 3000);
  }
});
