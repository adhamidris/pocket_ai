document.addEventListener("DOMContentLoaded", () => {
    const targetElement = document.querySelector(".flip-words-target");
    if (!targetElement) return;

    const words = ["Support", "Assistants"];
    let currentWordIndex = 0;

    // Create a wrapper
    const wrapper = document.createElement("div");
    wrapper.className = "flip-words-wrapper";
    targetElement.innerHTML = "";
    targetElement.appendChild(wrapper);

    // Function to wrap letters
    const createWordSpan = (text) => {
        const wordSpan = document.createElement("span");
        wordSpan.className = "flip-word text-primary";

        const subWords = text.split(" ");
        subWords.forEach((subWord, swIdx) => {
            const subWordSpan = document.createElement("span");
            subWordSpan.className = "inline-block whitespace-nowrap";

            subWord.split("").forEach((char) => {
                const letterSpan = document.createElement("span");
                letterSpan.textContent = char;
                letterSpan.className = "flip-letter";
                subWordSpan.appendChild(letterSpan);
            });

            wordSpan.appendChild(subWordSpan);

            // Add space between subwords
            if (swIdx < subWords.length - 1) {
                const space = document.createElement("span");
                space.innerHTML = "&nbsp;";
                space.className = "inline-block";
                wordSpan.appendChild(space);
            }
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
        // CRITICAL: Clear all animation delays so letters exit simultaneously
        const letters = wordSpan.querySelectorAll(".flip-letter");
        letters.forEach((letter) => {
            letter.style.animationDelay = '0s';
        });

        // Remove after exit animation completes (0.2s animation + small buffer)
        setTimeout(() => {
            if (wordSpan.parentNode) {
                wordSpan.remove();
            }
        }, 250);
    };

    const cycleWords = () => {
        const currentWord = words[currentWordIndex];
        const newWordSpan = createWordSpan(currentWord);

        // Find and animate out ALL existing words (not just non-exiting ones)
        const existingWords = wrapper.querySelectorAll(".flip-word");
        existingWords.forEach(oldWordSpan => {
            if (!oldWordSpan.classList.contains("exiting")) {
                oldWordSpan.classList.add("exiting");
                animateOut(oldWordSpan);
            }
        });

        // Add and animate in the new word
        wrapper.appendChild(newWordSpan);
        animateIn(newWordSpan);

        currentWordIndex = (currentWordIndex + 1) % words.length;
    };

    // Initial start
    cycleWords();

    // Loop every 3 seconds
    setInterval(cycleWords, 3000);
});
