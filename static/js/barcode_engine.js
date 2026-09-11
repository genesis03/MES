/**
 * ==========================================================================
 * Code 128 Auto 인코딩 및 고해상도 SVG 바코드 생성 엔진
 * ==========================================================================
 */

const Code128Engine = {
    PATTERNS: [
        "212222","222122","222221","121223","121322","131222","122213","122312","132212","221213",
        "221312","231212","112232","122132","122231","113222","123122","123221","223211","221132",
        "221231","213212","223112","312131","311222","321122","321221","312212","322112","322211",
        "212123","212321","232121","111323","131123","131321","112313","132113","132311","211313",
        "231113","231311","112133","112331","132131","113123","113321","133121","313121","211331",
        "231131","213113","213311","213131","311123","311321","331121","312113","312311","332111",
        "314111","221411","431111","111224","111422","121124","121421","141122","141221","112214",
        "112412","122114","122411","142112","142211","241211","221114","413111","241112","134111",
        "111242","121142","121241","114212","124112","124211","411212","421112","421211","212141",
        "214121","412121","111143","111341","131141","114113","114311","411113","411311","113141",
        "114131","311141","411131","211412","211214","211232","2331112"
    ],

    encode(text) {
        if (!text) return [];
        const tokens = [];
        let i = 0;

        while (i < text.length) {
            const isD = text.charCodeAt(i) >= 48 && text.charCodeAt(i) <= 57;
            const start = i;
            while (i < text.length && ((text.charCodeAt(i) >= 48 && text.charCodeAt(i) <= 57) === isD)) {
                i++;
            }
            tokens.push({ isDigits: isD, val: text.substring(start, i) });
        }

        const codes = [];
        let currentMode = (tokens[0].isDigits && tokens[0].val.length >= 4) ? 'C' : 'B';
        codes.push(currentMode === 'C' ? 105 : 104);

        for (let t = 0; t < tokens.length; t++) {
            const token = tokens[t];
            if (!token.isDigits) {
                if (currentMode === 'C') {
                    codes.push(100);
                    currentMode = 'B';
                }
                for (let c = 0; c < token.val.length; c++) {
                    codes.push(token.val.charCodeAt(c) - 32);
                }
            } else {
                const digits = token.val;
                let idx = 0;
                while (idx < digits.length) {
                    const rem = digits.length - idx;
                    if (currentMode === 'B') {
                        if (rem >= 4) {
                            codes.push(99);
                            currentMode = 'C';
                            codes.push(parseInt(digits.substring(idx, idx + 2), 10));
                            idx += 2;
                        } else {
                            codes.push(digits.charCodeAt(idx) - 32);
                            idx += 1;
                        }
                    } else {
                        if (rem >= 2) {
                            codes.push(parseInt(digits.substring(idx, idx + 2), 10));
                            idx += 2;
                        } else {
                            codes.push(100);
                            currentMode = 'B';
                            codes.push(digits.charCodeAt(idx) - 32);
                            idx += 1;
                        }
                    }
                }
            }
        }

        let chk = codes[0];
        for (let k = 1; k < codes.length; k++) {
            chk += k * codes[k];
        }
        codes.push(chk % 103, 106);
        return codes;
    },

    createSVG(text) {
        const codes = this.encode(text);
        if (codes.length === 0) return "";

        let sequence = "";
        for (let i = 0; i < codes.length; i++) {
            sequence += this.PATTERNS[codes[i]];
        }

        const quietZone = 10;
        const barHeight = 70;
        let x = quietZone;
        let rects = "";

        for (let i = 0; i < sequence.length; i++) {
            const width = parseInt(sequence[i], 10);
            if (i % 2 === 0) {
                rects += `<rect x="${x}" y="0" width="${width}" height="${barHeight}" fill="#000000" />`;
            }
            x += width;
        }

        const totalWidth = x + quietZone;
        return `<svg viewBox="0 0 ${totalWidth} ${barHeight}" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg" class="barcode-svg">
            <rect width="${totalWidth}" height="${barHeight}" fill="#ffffff" />
            ${rects}
        </svg>`;
    }
};