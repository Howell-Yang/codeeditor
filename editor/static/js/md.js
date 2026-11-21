/*
 * A small, safe Markdown renderer for problem statements.
 *
 * Everything is HTML-escaped first and only a fixed set of tags is produced,
 * so text typed by anybody in a room can be shown to everybody else. No raw
 * HTML, no images (they would let one participant track the others).
 *
 * Supported: headings, paragraphs (single line breaks are kept), bold,
 * italic, strike-through, inline code, fenced code blocks, links, ordered and
 * unordered lists (two levels), block quotes, tables, horizontal rules.
 */
(function (root, factory) {
  'use strict';
  var render = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = render;
  } else {
    root.CodeTool = root.CodeTool || {};
    root.CodeTool.markdown = render;
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  function escapeHtml(text) {
    return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  var SAFE_URL = /^(https?:\/\/|mailto:|\/(?!\/)|#)/i;

  function inline(text) {
    // 1. Take code spans out so that nothing inside them is interpreted.
    var codes = [];
    text = text.replace(/(`+)([\s\S]*?[^`])\1(?!`)/g, function (match, ticks, code) {
      codes.push('<code>' + escapeHtml(code.replace(/^ (.*) $/, '$1')) + '</code>');
      return '\u0000' + (codes.length - 1) + '\u0000';
    });
    // 2. Escape everything else.
    text = escapeHtml(text);
    // 3. Links: [label](url) and bare URLs.
    var links = [];
    text = text.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)|(https?:\/\/[^\s<]+[^\s<.,;:!?)\]}'"，。；：！？）】])/g,
      function (match, label, url, bare) {
        var href = bare || url;
        if (!SAFE_URL.test(href)) { return match; }
        links.push('<a href="' + href + '" target="_blank" rel="noopener noreferrer nofollow">' +
          (bare || label) + '</a>');
        return '\u0001' + (links.length - 1) + '\u0001';
      });
    // 4. Emphasis.
    text = text
      .replace(/\*\*([^\s*](?:[^*]*[^\s*])?)\*\*/g, '<strong>$1</strong>')
      .replace(/__([^\s_](?:[^_]*[^\s_])?)__/g, '<strong>$1</strong>')
      .replace(/~~([^~\n]+)~~/g, '<del>$1</del>')
      .replace(/(^|[^*\w])\*([^\s*](?:[^*\n]*[^\s*])?)\*(?!\*)/g, '$1<em>$2</em>')
      .replace(/(^|[^_\w])_([^\s_](?:[^_\n]*[^\s_])?)_(?![_\w])/g, '$1<em>$2</em>');
    // 5. Put links and code back.
    text = text.replace(/\u0001(\d+)\u0001/g, function (match, index) { return links[+index]; });
    return text.replace(/\u0000(\d+)\u0000/g, function (match, index) { return codes[+index]; });
  }

  var FENCE = /^ {0,3}(```+|~~~+)\s*([\w#+.-]*)\s*$/;
  var HEADING = /^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
  var RULE = /^ {0,3}([-*_])(?:\s*\1){2,}\s*$/;
  var LIST_ITEM = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
  var QUOTE = /^ {0,3}>\s?(.*)$/;
  var TABLE_DIVIDER = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

  function splitRow(line) {
    var trimmed = line.trim().replace(/^\|/, '').replace(/\|$/, '');
    return trimmed.split('|').map(function (cell) { return cell.trim(); });
  }

  function startsBlock(line, next) {
    return FENCE.test(line) || HEADING.test(line) || RULE.test(line) || LIST_ITEM.test(line) ||
      QUOTE.test(line) || (line.indexOf('|') !== -1 && next !== undefined && TABLE_DIVIDER.test(next));
  }

  function renderList(lines) {
    // lines: [{indent, ordered, text}] - at most two levels are distinguished.
    var html = '';
    var baseIndent = lines[0].indent;
    var index = 0;
    var ordered = lines[0].ordered;
    html += ordered ? '<ol>' : '<ul>';
    while (index < lines.length) {
      var item = lines[index];
      var children = [];
      index++;
      while (index < lines.length && lines[index].indent >= baseIndent + 2) {
        children.push(lines[index]);
        index++;
      }
      html += '<li>' + inline(item.text) + (children.length ? renderList(children) : '') + '</li>';
    }
    return html + (ordered ? '</ol>' : '</ul>');
  }

  function render(source) {
    var lines = String(source == null ? '' : source)
      .replace(/[\u0000\u0001]/g, '')   // reserved as placeholders in inline()
      .replace(/\r\n?/g, '\n').replace(/\t/g, '    ').split('\n');
    var html = [];
    var i = 0;
    while (i < lines.length) {
      var line = lines[i];
      var match;

      if (/^\s*$/.test(line)) { i++; continue; }

      if ((match = FENCE.exec(line))) {
        var fence = match[1];
        var language = match[2];
        var closing = new RegExp('^ {0,3}' + (fence.charAt(0) === '`' ? '`' : '~') + '{' + fence.length + ',}\\s*$');
        var body = [];
        i++;
        while (i < lines.length && !closing.test(lines[i])) {
          body.push(lines[i]);
          i++;
        }
        i++; // closing fence (or end of text)
        html.push('<pre><code' + (language ? ' class="lang-' + escapeHtml(language) + '"' : '') + '>' +
          escapeHtml(body.join('\n')) + '</code></pre>');
        continue;
      }

      if ((match = HEADING.exec(line))) {
        var level = match[1].length;
        html.push('<h' + level + '>' + inline(match[2]) + '</h' + level + '>');
        i++;
        continue;
      }

      if (RULE.test(line)) { html.push('<hr>'); i++; continue; }

      if (QUOTE.test(line)) {
        var quoted = [];
        while (i < lines.length && (match = QUOTE.exec(lines[i]))) { quoted.push(match[1]); i++; }
        html.push('<blockquote>' + render(quoted.join('\n')) + '</blockquote>');
        continue;
      }

      if (line.indexOf('|') !== -1 && i + 1 < lines.length && TABLE_DIVIDER.test(lines[i + 1])) {
        var header = splitRow(line);
        var table = '<div class="md-table"><table><thead><tr>' + header.map(function (cell) {
          return '<th>' + inline(cell) + '</th>';
        }).join('') + '</tr></thead><tbody>';
        i += 2;
        while (i < lines.length && lines[i].indexOf('|') !== -1 && !/^\s*$/.test(lines[i])) {
          var cells = splitRow(lines[i]);
          table += '<tr>';
          for (var c = 0; c < header.length; c++) { table += '<td>' + inline(cells[c] || '') + '</td>'; }
          table += '</tr>';
          i++;
        }
        html.push(table + '</tbody></table></div>');
        continue;
      }

      if ((match = LIST_ITEM.exec(line))) {
        var items = [];
        while (i < lines.length) {
          var itemMatch = LIST_ITEM.exec(lines[i]);
          if (itemMatch) {
            items.push({ indent: itemMatch[1].length, ordered: /\d/.test(itemMatch[2]), text: itemMatch[3] });
            i++;
          } else if (!/^\s*$/.test(lines[i]) && /^\s{2,}/.test(lines[i]) && items.length) {
            items[items.length - 1].text += '\n' + lines[i].trim();   // continuation line
            i++;
          } else {
            break;
          }
        }
        html.push(renderList(items));
        continue;
      }

      var paragraph = [];
      while (i < lines.length && !/^\s*$/.test(lines[i]) &&
          !(paragraph.length && startsBlock(lines[i], lines[i + 1]))) {
        paragraph.push(lines[i].replace(/\s+$/, ''));
        i++;
      }
      html.push('<p>' + inline(paragraph.join('\n')).replace(/\n/g, '<br>') + '</p>');
    }
    return html.join('\n');
  }

  return render;
}));
