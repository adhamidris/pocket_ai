document.addEventListener("DOMContentLoaded", () => {
    const targetElement = document.querySelector(".flip-words-target");
    if (!targetElement) return;

    const words = ["Customer Service", "Assistants"];
    let currentWordIndex = 0;

    // Create a wrapper
    const wrapper = document.createElement("div");
    wrapper.className = "flip-words-wrapper";
    // Insert wrapper before target and put target inside, or just replace content?
    // Let's replace the content of targetElement directly but styled properly
    targetElement.innerHTML = "";
    targetElement.appendChild(wrapper);

    // Function to wrap letters
    const createWordSpan = (text) => {
        const wordSpan = document.createElement("span");
        wordSpan.className = "flip-word text-primary"; // Fallback to solid color

        // Split by space to handle multi-word phrases as single "flip word" logical unit if needed,
        // but the reference splits by word then letter.
        // "Customer Service" is one item in the array `words`.
        // The reference splits the current phrase into sub-words (space delimited)
        // and then splits those into letters.

        const subWords = text.split(" ");
        subWords.forEach((subWord, swIdx) => {
            const subWordSpan = document.createElement("span");
            subWordSpan.className = "inline-block whitespace-nowrap"; // Keep sub-words together

            subWord.split("").forEach((char, charIdx) => {
                const letterSpan = document.createElement("span");
                letterSpan.textContent = char;
                letterSpan.className = "flip-letter";
                subWordSpan.appendChild(letterSpan);
            });

            wordSpan.appendChild(subWordSpan);

            // Add space if not last subword
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
            letter.style.animationDelay = `${i * 0.05}s`;
            letter.classList.add("active");
        });
    };

    const animateOut = (wordSpan, callback) => {
        const letters = wordSpan.querySelectorAll(".flip-letter");
        // We are animating the whole container out now via CSS on the .exiting class
        // by targeting .exiting .flip-letter

        // Clean up hardcoded styles - relying on CSS Grid stacking
        // wordSpan.style.position = "absolute"; // Removed

        setTimeout(() => {
            if (callback) callback();
        }, 600); // Wait for animation to finish (approx 0.4s + delays)
    };

    const cycleWords = () => {
        const currentWord = words[currentWordIndex];
        const newWordSpan = createWordSpan(currentWord);

        // Position absolute for exit requires the wrapper to be relative.
        // When a new word comes in, the old one (if exists) needs to exit.

        const oldWordSpan = wrapper.querySelector(".flip-word:not(.exiting)");

        if (oldWordSpan) {
            oldWordSpan.classList.add("exiting"); // Marker class
            animateOut(oldWordSpan, () => {
                oldWordSpan.remove();
            });
        }

        wrapper.appendChild(newWordSpan);
        animateIn(newWordSpan);

        currentWordIndex = (currentWordIndex + 1) % words.length;
    };

    // Initial start
    cycleWords();

    // Loop
    setInterval(cycleWords, 3000);
});
