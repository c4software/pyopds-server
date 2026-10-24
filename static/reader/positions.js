// Web reader: positions in a chapter (spine item) document shared with KOReader and CrossPoint readers.
// - XPointer of KOReader (crengine), also the main position of CrossPoint readers, written like the
//   firmware does: /body/DocFragment[<spine index + 1>]/body/div[2]/p[4]/text()[1].17 (element steps
//   counted among their siblings of the same name, text nodes among the element's text children,
//   offset in code points). KOReader reads them too; its own (no [1], blank text nodes skipped) are read.
// - Visible offsets of CrossPoint clippings: code points of every text node of the <body> outside
//   head/style/script/title/rp, whitespace included (ChapterHtmlSlimParser of the firmware).
// - Paragraph index of CrossPoint positions: 1-based rank of the <p> / <li> holding the position.
// Pure DOM functions on the chapter's document (foliate-js renders the XHTML as is, no element added
// to the body); plain script registered in window.PyReader.
(function () {
    'use strict';

    const PyReader = window.PyReader = window.PyReader || {};

    const NON_VISIBLE = new Set(['head', 'style', 'script', 'title', 'rp']);
    const SURROGATES = /[\uD800-\uDBFF][\uDC00-\uDFFF]/g;
    const XPOINTER_RE = /^\/body\/DocFragment\[(\d+)\](.*)$/;
    const BLANK_RE = /^[ \t\r\n]*$/;

    /** Number of code points of a string (KOReader and CrossPoint count code points, JS strings UTF-16 units). */
    function codePoints(text) {
        const pairs = text.match(SURROGATES);
        return text.length - (pairs ? pairs.length : 0);
    }

    /** UTF-16 index of the code point `count` of a string (clamped to its length). */
    function unitIndex(text, count) {
        let units = 0;
        for (let i = 0; i < count && units < text.length; i++) {
            const code = text.charCodeAt(units);
            units += code >= 0xD800 && code <= 0xDBFF && units + 1 < text.length ? 2 : 1;
        }
        return units;
    }

    function name(node) {
        return (node.localName || '').toLowerCase();
    }

    function isBlank(node) {
        return BLANK_RE.test(node.data);
    }

    /** Text nodes of the body counted by CrossPoint (CDATA included, non-visible elements skipped), in order. */
    function visibleTextNodes(doc) {
        const body = doc.body || doc.querySelector('body');
        if (!body) return [];
        const walker = doc.createTreeWalker(body, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT | NodeFilter.SHOW_CDATA_SECTION, {
            acceptNode: function (node) {
                if (node.nodeType !== Node.ELEMENT_NODE) return NodeFilter.FILTER_ACCEPT;
                return NON_VISIBLE.has(name(node)) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_SKIP;
            },
        });
        const nodes = [];
        while (walker.nextNode()) nodes.push(walker.currentNode);
        return nodes;
    }

    /**
     * Text node and UTF-16 offset of a DOM point (an element point goes to the first text at or after it,
     * null when there is none).
     */
    function textPoint(doc, node, offset) {
        if (node.nodeType === Node.TEXT_NODE || node.nodeType === Node.CDATA_SECTION_NODE) return { node: node, offset: offset };
        const nodes = visibleTextNodes(doc);
        const boundary = doc.createRange();
        boundary.setStart(node, offset);
        const next = nodes.find(function (text) { return boundary.comparePoint(text, 0) >= 0; });
        return next ? { node: next, offset: 0 } : null;
    }

    /** Visible offset (code points from the start of the body) of a DOM point. */
    function visibleOffset(doc, node, offset) {
        const point = textPoint(doc, node, offset);
        const nodes = visibleTextNodes(doc);
        if (!point) return nodes.reduce(function (sum, text) { return sum + codePoints(text.data); }, 0);
        let total = 0;
        for (const text of nodes) {
            if (text === point.node) return total + codePoints(text.data.slice(0, point.offset));
            total += codePoints(text.data);
        }
        return total;
    }

    /** DOM point ({node, offset}) of a visible offset, null when the chapter has no text. */
    function pointAt(doc, target, atEnd) {
        const nodes = visibleTextNodes(doc);
        let total = 0;
        for (const text of nodes) {
            const length = codePoints(text.data);
            // An end offset stays in the text it closes; a start offset goes to the next text
            if (atEnd ? target <= total + length : target < total + length) {
                return { node: text, offset: unitIndex(text.data, Math.max(0, target - total)) };
            }
            total += length;
        }
        const last = nodes[nodes.length - 1];
        return last ? { node: last, offset: last.data.length } : null;
    }

    /** {start, end} visible offsets of a Range (CrossPoint clipping offsets). */
    function rangeOffsets(doc, range) {
        return {
            start: visibleOffset(doc, range.startContainer, range.startOffset),
            end: visibleOffset(doc, range.endContainer, range.endOffset),
        };
    }

    /** Range of visible offsets, null when they do not fit the chapter. */
    function offsetsRange(doc, start, end) {
        const from = pointAt(doc, start, false);
        const to = pointAt(doc, end, true);
        if (!from || !to) return null;
        const range = doc.createRange();
        range.setStart(from.node, from.offset);
        range.setEnd(to.node, to.offset);
        return range.collapsed ? null : range;
    }

    /** Text children of an element (CDATA included), blank ones with `withBlank`. */
    function textChildren(element, withBlank) {
        return Array.prototype.filter.call(element.childNodes, function (child) {
            return (child.nodeType === Node.TEXT_NODE || child.nodeType === Node.CDATA_SECTION_NODE)
                && child.data.length > 0 && (withBlank || !isBlank(child));
        });
    }

    /**
     * KOReader XPointer of a DOM point in the chapter of spine index `spine` (a blank text, which no
     * reader can address, moves to the start of the next text).
     */
    function toXPointer(doc, spine, node, offset) {
        const prefix = '/body/DocFragment[' + (spine + 1) + ']/body';
        let point = textPoint(doc, node, offset);
        if (point && isBlank(point.node)) {
            const nodes = visibleTextNodes(doc);
            const next = nodes.slice(nodes.indexOf(point.node) + 1).find(function (text) { return !isBlank(text); });
            point = next ? { node: next, offset: 0 } : null;
        }
        if (!point) return prefix;
        const steps = [];
        let element = point.node.parentNode;
        while (element && element.nodeType === Node.ELEMENT_NODE && name(element) !== 'body') {
            const tag = name(element);
            const same = Array.prototype.filter.call(element.parentNode.children, function (sibling) { return name(sibling) === tag; });
            steps.unshift(tag + '[' + (same.indexOf(element) + 1) + ']');
            element = element.parentNode;
        }
        const texts = textChildren(point.node.parentNode, true);
        const chars = codePoints(point.node.data.slice(0, point.offset));
        return prefix + (steps.length ? '/' + steps.join('/') : '') + '/text()[' + (texts.indexOf(point.node) + 1) + '].' + chars;
    }

    /** Spine index (0-based) of a KOReader XPointer, null when it is not one. */
    function xpointerSpine(xpointer) {
        const match = XPOINTER_RE.exec(xpointer || '');
        return match ? Number(match[1]) - 1 : null;
    }

    /**
     * DOM point ({node, offset}) of a KOReader XPointer in its chapter document; a step missing from the
     * document stops at the deepest element found (same book, different edition or rendering).
     */
    function fromXPointer(doc, xpointer) {
        const match = XPOINTER_RE.exec(xpointer || '');
        const body = doc.body || doc.querySelector('body');
        if (!match || !body) return null;
        let path = match[2];
        let charOffset = 0;
        const tail = /\.(\d+)$/.exec(path);
        if (tail) {
            charOffset = Number(tail[1]);
            path = path.slice(0, tail.index);
        }
        const steps = path.split('/').filter(Boolean);
        if (steps[0] === 'body') steps.shift();
        let element = body;
        let textIndex = null;
        for (const step of steps) {
            const parts = /^([^[\]]+)(?:\[(\d+)\])?$/.exec(step);
            if (!parts) break;
            const index = parts[2] ? Number(parts[2]) : 1;
            if (parts[1] === 'text()') {
                textIndex = index;
                break;
            }
            const tag = parts[1].toLowerCase();
            const same = Array.prototype.filter.call(element.children, function (child) { return name(child) === tag; });
            const child = same[index - 1] || same[0];
            if (!child) break;
            element = child;
        }
        if (textIndex === null) {
            // Element point: its first visible text
            const point = textPoint(doc, element, 0);
            return point ? { node: point.node, offset: 0 } : null;
        }
        // Text nodes counted like CrossPoint (blank ones included), else like KOReader (skipped)
        const fits = function (text) { return text && !isBlank(text) && codePoints(text.data) >= charOffset; };
        const candidates = [textChildren(element, true)[textIndex - 1], textChildren(element, false)[textIndex - 1]];
        const text = candidates.find(fits) || candidates.find(Boolean) || textChildren(element, false)[0];
        if (!text) {
            const point = textPoint(doc, element, 0);
            return point ? { node: point.node, offset: 0 } : null;
        }
        return { node: text, offset: unitIndex(text.data, charOffset) };
    }

    /** Collapsed Range of a KOReader XPointer, null when it cannot be resolved. */
    function xpointerRange(doc, xpointer) {
        const point = fromXPointer(doc, xpointer);
        if (!point) return null;
        const range = doc.createRange();
        range.setStart(point.node, Math.min(point.offset, point.node.data.length));
        range.collapse(true);
        return range;
    }

    /** CrossPoint paragraph index: rank (from 1) of the last <p> / <li> starting at or before a DOM point, 0 when none. */
    function paragraphIndex(doc, node, offset) {
        const body = doc.body || doc.querySelector('body');
        if (!body) return 0;
        const point = doc.createRange();
        point.setStart(node, offset);
        let count = 0;
        const walker = doc.createTreeWalker(body, NodeFilter.SHOW_ELEMENT);
        while (walker.nextNode()) {
            const element = walker.currentNode;
            const tag = name(element);
            if (tag !== 'p' && tag !== 'li') continue;
            // Elements starting after the point end the count
            if (point.comparePoint(element, 0) > 0) break;
            count++;
        }
        return count;
    }

    PyReader.positions = {
        codePoints: codePoints,
        visibleTextNodes: visibleTextNodes,
        visibleOffset: visibleOffset,
        rangeOffsets: rangeOffsets,
        offsetsRange: offsetsRange,
        toXPointer: toXPointer,
        fromXPointer: fromXPointer,
        xpointerRange: xpointerRange,
        xpointerSpine: xpointerSpine,
        paragraphIndex: paragraphIndex,
    };
})();
