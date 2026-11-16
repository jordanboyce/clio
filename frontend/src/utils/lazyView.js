import { defineAsyncComponent, defineComponent, h, ref } from 'vue'
import LazyViewFallback from '../components/LazyViewFallback.vue'

/**
 * A view that ships in its own chunk.
 *
 * While the chunk loads the pane says what it is opening, so a slow first
 * open does not look like an empty tab. Hashed asset names plus an immutable
 * cache mean a tab left open across a deploy asks the new server for a chunk
 * that no longer exists, and Vue's default for a failed chunk is to render
 * nothing at all. One automatic retry covers a dropped connection; past that
 * the pane says what happened and offers Try again (a fresh load, keeping
 * everything else on the page) and the reload that fixes a deploy.
 *
 * defineAsyncComponent remembers a failed load for good, so Try again builds
 * a new one and remounts it under a new key.
 */
export function lazyView(loader, { name = '' } = {}) {
  const attempt = ref(0)
  const retry = () => {
    view = build()
    attempt.value++
  }
  const build = () => defineAsyncComponent({
    loader,
    loadingComponent: () => h(LazyViewFallback, { name }),
    errorComponent: () => h(LazyViewFallback, { name, failed: true, onRetry: retry }),
    delay: 150,
    timeout: 20000,
    onError(error, retryLoad, fail, attempts) {
      if (attempts <= 1) retryLoad()
      else fail(error)
    },
  })
  let view = build()

  return defineComponent({
    name: 'LazyView',
    inheritAttrs: false,
    setup(_, { attrs, slots }) {
      return () => h(view, { ...attrs, key: attempt.value }, slots)
    },
  })
}
