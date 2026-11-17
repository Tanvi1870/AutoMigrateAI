import { describe, expect, it } from 'vitest'
import { highlight } from './CodeView.jsx'

const text = (line) => line.map((token) => token.text).join('')
const classesOf = (lines) => lines.flat().filter((token) => token.cls).map((token) => [token.cls, token.text])

describe('code highlighter', () => {
  it('keeps every line and character of the source', () => {
    const source = 'import pytest\n\n\ndef test_login(page):\n    page.goto("x")  # go\n'
    const lines = highlight(source, 'tests/login_test.py')
    expect(lines.map(text).join('\n')).toBe(source)
    expect(lines).toHaveLength(source.split('\n').length)
  })

  it('colours Python keywords, strings, comments, numbers and decorators', () => {
    const lines = highlight('@pytest.mark.smoke\ndef f(x=1):\n    return "a"  # done\n', 'a.py')
    expect(classesOf(lines)).toEqual([
      ['tk-decorator', '@pytest.mark.smoke'],
      ['tk-keyword', 'def'],
      ['tk-number', '1'],
      ['tk-keyword', 'return'],
      ['tk-string', '"a"'],
      ['tk-comment', '# done'],
    ])
  })

  it('keeps multi-line strings coloured on every line', () => {
    const lines = highlight('x = """one\ntwo"""\n', 'a.py')
    expect(lines[0].at(-1)).toEqual({ text: '"""one', cls: 'tk-string' })
    expect(lines[1]).toEqual([{ text: 'two"""', cls: 'tk-string' }])
  })

  it('colours JSON keys and values, and leaves other files plain', () => {
    expect(classesOf(highlight('{"a": "b", "n": 2, "t": true}', 'data.json'))).toEqual([
      ['tk-key', '"a"'],
      ['tk-string', '"b"'],
      ['tk-key', '"n"'],
      ['tk-number', '2'],
      ['tk-key', '"t"'],
      ['tk-const', 'true'],
    ])
    expect(classesOf(highlight('def x', 'README'))).toEqual([])
  })

  it('treats markup in generated code as text, never as HTML', () => {
    const source = 'x = "<img src=x onerror=alert(1)>"'
    const lines = highlight(source, 'a.py')
    expect(text(lines[0])).toBe(source)
    expect(lines.flat().every((token) => typeof token.text === 'string')).toBe(true)
  })
})
