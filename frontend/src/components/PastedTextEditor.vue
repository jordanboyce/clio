<template>
  <!-- Text typed or pasted straight into the collection, the way a notebook
       app takes "copied text" as a source. A small rich-text box rather than
       a textarea, because what people paste (a web page, a Word document, an
       email) arrives with headings, lists and emphasis worth keeping, and
       the server cites a long note by its headings. -->
  <div class="space-y-1.5" data-testid="text-panel">
    <input
      v-model="title"
      type="text"
      class="input input-bordered input-xs w-full"
      placeholder="Title (the first line, when left blank)"
      aria-label="Title for the text"
      maxlength="200"
      :disabled="busy"
      @keydown.enter.prevent="focusEditor"
    />
    <div class="rounded border border-base-300 bg-base-100 focus-within:border-primary/60">
      <div class="flex items-center gap-0.5 px-1 py-0.5 border-b border-base-300 flex-wrap" role="toolbar" aria-label="Formatting">
        <button v-for="tool in TOOLS" :key="tool.label" type="button" class="side-icon-btn side-icon-btn-sm text-base-content/60 hover:text-base-content"
                :title="tool.title" :aria-label="tool.label" :disabled="busy"
                @mousedown.prevent @click="format(tool)">
          <component :is="tool.icon" :size="12" />
        </button>
      </div>
      <div
        ref="editor"
        class="rich-text paste-editor"
        contenteditable="true"
        role="textbox"
        aria-multiline="true"
        aria-label="Text to add"
        data-placeholder="Type, or paste from a page, a document or an email…"
        :aria-disabled="busy || undefined"
        @input="sync"
        @paste="onPaste"
        @keydown="onKeydown"
      ></div>
    </div>
    <div class="flex items-center gap-2 text-xs">
      <span class="flex-1 text-base-content/50 leading-tight">
        <template v-if="chars > 0">{{ chars.toLocaleString() }} characters. </template>Headings, lists, links and emphasis are kept; the note is cited by section.
      </span>
      <button class="btn btn-primary btn-xs gap-1 flex-shrink-0" @click="submit" :disabled="busy || chars === 0" :title="`Add this text  (${modKey} Enter)`">
        <span v-if="busy" class="loading loading-spinner loading-xs"></span>
        <ClipboardPaste v-else :size="11" />
        {{ busy ? 'Adding…' : 'Add text' }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { Bold, ClipboardPaste, Code, Heading2, Italic, List, ListOrdered, RemoveFormatting, TextQuote } from 'lucide-vue-next'
import { plainTextToHtml, readableLength, sanitizeRichText } from '../utils/richText'

const props = defineProps({
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['submit'])

const editor = ref(null)
const title = ref('')
const html = ref('')
const chars = computed(() => readableLength(html.value))
const modKey = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || '') ? '⌘' : 'Ctrl'

// execCommand is old, but every browser still runs it and it keeps this
// editor at a few lines instead of a dependency: the server reduces whatever
// it produces to the same allowlist anyway.
const TOOLS = [
  { label: 'Bold', title: 'Bold (Ctrl B)', icon: Bold, command: 'bold' },
  { label: 'Italic', title: 'Italic (Ctrl I)', icon: Italic, command: 'italic' },
  { label: 'Heading', title: 'Heading', icon: Heading2, command: 'formatBlock', value: 'h2', toggles: 'h2' },
  { label: 'Bulleted list', title: 'Bulleted list', icon: List, command: 'insertUnorderedList' },
  { label: 'Numbered list', title: 'Numbered list', icon: ListOrdered, command: 'insertOrderedList' },
  { label: 'Quote', title: 'Quote', icon: TextQuote, command: 'formatBlock', value: 'blockquote', toggles: 'blockquote' },
  { label: 'Code block', title: 'Code block', icon: Code, command: 'formatBlock', value: 'pre', toggles: 'pre' },
  { label: 'Clear formatting', title: 'Clear formatting', icon: RemoveFormatting, command: 'removeFormat' },
]

const sync = () => { html.value = editor.value ? editor.value.innerHTML : '' }

const focusEditor = () => editor.value?.focus()

const blockAtCaret = () => {
  const sel = window.getSelection?.()
  let node = sel && sel.rangeCount ? sel.getRangeAt(0).startContainer : null
  while (node && node !== editor.value) {
    if (node.nodeType === 1 && /^(H[1-6]|BLOCKQUOTE|PRE|P|DIV)$/.test(node.tagName)) return node.tagName.toLowerCase()
    node = node.parentNode
  }
  return ''
}

const format = (tool) => {
  if (!editor.value || typeof document.execCommand !== 'function') return
  editor.value.focus()
  let value = tool.value
  // A second press of Heading / Quote / Code turns the block back into a paragraph.
  if (tool.toggles && blockAtCaret() === tool.toggles) value = 'p'
  document.execCommand(tool.command, false, value)
  sync()
}

// Insert markup where the caret is (or at the end when the editor has no
// selection), without execCommand('insertHTML'), which some browsers mangle.
const insertHtml = (markup) => {
  const box = editor.value
  if (!box) return
  const sel = window.getSelection?.()
  const range = sel && sel.rangeCount ? sel.getRangeAt(0) : null
  if (range && box.contains(range.commonAncestorContainer)) {
    range.deleteContents()
    const fragment = range.createContextualFragment(markup)
    const last = fragment.lastChild
    range.insertNode(fragment)
    if (last) {
      range.setStartAfter(last)
      range.collapse(true)
      sel.removeAllRanges()
      sel.addRange(range)
    }
  } else {
    box.insertAdjacentHTML('beforeend', markup)
    // Typing then carries on after what was pasted.
    if (sel) {
      const end = document.createRange()
      end.selectNodeContents(box)
      end.collapse(false)
      sel.removeAllRanges()
      sel.addRange(end)
    }
  }
  sync()
}

const onPaste = (event) => {
  const data = event.clipboardData
  if (!data) return
  const rich = data.getData('text/html')
  const plain = data.getData('text/plain')
  if (!rich && !plain) return
  event.preventDefault()
  const markup = rich ? sanitizeRichText(rich) : plainTextToHtml(plain)
  // A one-line paste (a sentence, a URL) goes inline, not as its own paragraph.
  insertHtml(!rich && !plain.includes('\n') ? markup.replace(/^<p>|<\/p>$/g, '') : markup)
}

const onKeydown = (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
    event.preventDefault()
    submit()
  }
}

const submit = () => {
  if (props.busy || chars.value === 0) return
  emit('submit', { title: title.value.trim(), html: sanitizeRichText(html.value), chars: chars.value })
}

// The parent empties the editor once the note is queued; a failed post keeps
// what was written so nobody has to paste it again.
const clear = () => {
  title.value = ''
  if (editor.value) editor.value.innerHTML = ''
  html.value = ''
}
defineExpose({ clear, focus: focusEditor })

onMounted(() => nextTick(focusEditor))
</script>

<style scoped>
.paste-editor {
  min-height: 9rem;
  max-height: 40vh;
  overflow-y: auto;
  padding: 0.5rem 0.6rem;
  font-size: 0.75rem;
  line-height: 1.55;
  outline: none;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.paste-editor:empty::before {
  content: attr(data-placeholder);
  color: color-mix(in oklch, var(--color-base-content) 40%, transparent);
  pointer-events: none;
}
.paste-editor[aria-disabled="true"] { opacity: 0.6; pointer-events: none; }
</style>
