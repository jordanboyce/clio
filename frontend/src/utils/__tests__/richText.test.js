import { describe, expect, it } from 'vitest'
import { noteBodyHtml, plainTextToHtml, readableLength, sanitizeRichText } from '../richText'

describe('sanitizeRichText', () => {
  it('keeps the formatting a reader needs and nothing else', () => {
    const out = sanitizeRichText(
      '<div class="x" style="color:red" onclick="steal()"><h2 id="t">Title</h2>' +
      '<p>Some <b>bold</b> and <span style="font-weight:bold">styled</span> text.</p>' +
      '<script>alert(1)</script><img src="http://t.example/p.gif">' +
      '<a href="https://example.com/x" target="_blank" onmouseover="x()">link</a> ' +
      '<a href="javascript:alert(1)">bad</a> <a href="/relative">rel</a>' +
      '<table><tr><th>a</th><td>b</td></tr></table></div>',
    )
    expect(out).toBe(
      '<div><h2>Title</h2><p>Some <b>bold</b> and styled text.</p>' +
      '<a href="https://example.com/x">link</a> <a>bad</a> <a>rel</a>' +
      '<table><tbody><tr><th>a</th><td>b</td></tr></tbody></table></div>',
    )
  })

  it('reduces a word-processor paste to its text', () => {
    const pasted = "<html><head><style>p{margin:0}</style></head><body><!--StartFragment-->" +
      "<p class='MsoNormal'><span style='font-family:Calibri'>Hello</span> <font face='Arial'>world</font></p><!--EndFragment--></body></html>"
    expect(sanitizeRichText(pasted)).toBe('<p>Hello world</p>')
  })
})

describe('plainTextToHtml', () => {
  it('makes paragraphs of blank-line blocks and breaks of single newlines', () => {
    expect(plainTextToHtml('First line\nsecond line\n\n\nNext paragraph <tag>\r\n')).toBe(
      '<p>First line<br>second line</p><p>Next paragraph &lt;tag&gt;</p>',
    )
    expect(plainTextToHtml('  \n ')).toBe('')
  })
})

describe('noteBodyHtml', () => {
  it('draws the body of a stored note, cleaned, without its title', () => {
    const stored = '<!DOCTYPE html>\n<html><head><meta charset="utf-8"><title>Travel policy</title></head>' +
      '<body>\n<h1>Policy 12</h1><p>Two weeks.</p><script>x()</script>\n</body></html>'
    expect(noteBodyHtml(stored)).toBe('\n<h1>Policy 12</h1><p>Two weeks.</p>\n')
  })
})

describe('readableLength', () => {
  it('counts what a reader sees, whitespace folded', () => {
    expect(readableLength('<p>Hello   <b>world</b></p>\n<p><br></p>')).toBe('Hello world'.length)
    expect(readableLength('<p><br></p>')).toBe(0)
  })
})
