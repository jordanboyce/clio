<template>
  <!-- Admin-only: nothing renders unless this person can act on the queue. -->
  <div v-if="show && variant === 'header'" class="dropdown dropdown-end hidden md:block">
    <button
      tabindex="0"
      class="btn btn-ghost btn-circle btn-sm relative"
      :title="label"
      :aria-label="label"
      aria-haspopup="menu"
    >
      <Bell :size="16" aria-hidden="true" />
      <span
        v-if="review.total > 0"
        class="absolute -top-0.5 -right-0.5 min-w-4 h-4 px-1 rounded-full text-[10px] leading-4 font-semibold text-center tabular-nums text-white"
        :class="review.urgent ? 'bg-error' : 'bg-warning text-warning-content'"
        data-testid="review-badge"
      >{{ review.total > 99 ? '99+' : review.total }}</span>
    </button>
    <div tabindex="0" class="dropdown-content z-[60] bg-base-100 rounded-box shadow-lg border border-base-300 w-72 p-3">
      <div class="text-xs font-semibold uppercase tracking-wider text-base-content/55 mb-2">Needs review</div>
      <p v-if="review.total === 0" class="text-sm text-base-content/55 py-1">
        Nothing is waiting for you.
      </p>
      <ul v-else class="space-y-1">
        <li v-for="row in rows" :key="row.id">
          <button
            class="w-full flex items-center justify-between gap-3 px-2 py-1.5 rounded-md hover:bg-base-200 text-left text-sm"
            @click="go(row.target)"
          >
            <span>{{ row.label }}</span>
            <span class="badge badge-sm tabular-nums" :class="row.cls">{{ row.count }}</span>
          </button>
        </li>
      </ul>
      <button class="btn btn-xs btn-ghost w-full mt-2" @click="go('review')">Open the review queue</button>
    </div>
  </div>

  <button
    v-else-if="show && variant === 'footer'"
    class="flex items-center gap-1.5 transition-colors flex-shrink-0"
    :class="review.total > 0 ? (review.urgent ? 'text-error hover:text-error' : 'text-warning hover:text-warning') : 'hover:text-base-content'"
    :title="label"
    :aria-label="label"
    @click="go('review')"
  >
    <ShieldAlert v-if="review.total > 0" :size="11" aria-hidden="true" />
    <ShieldCheck v-else :size="11" aria-hidden="true" />
    <span v-if="review.total > 0" class="font-medium">{{ review.total }} to review</span>
    <span v-else>Review clear</span>
  </button>
</template>

<script setup>
import { computed } from 'vue'
import { Bell, ShieldAlert, ShieldCheck } from 'lucide-vue-next'
import { useReviewStore } from '../stores/reviewStore'
import { useUserStore } from '../stores/userStore'
import { reviewSummaryText } from '../utils/governance'

defineProps({
  variant: { type: String, default: 'header' }, // header | footer
})
const emit = defineEmits(['open'])

const review = useReviewStore()
const userStore = useUserStore()
const show = computed(() => userStore.adminConsole)

const label = computed(() => {
  if (review.total === 0) return 'Review queue — nothing waiting'
  return `${review.total} item${review.total === 1 ? '' : 's'} waiting for review: ${reviewSummaryText(review.summary, review.registrations)}`
})

const rows = computed(() => {
  const s = review.summary
  return [
    { id: 'held', label: 'Held from search', count: s.held, cls: 'badge-error', target: 'review' },
    { id: 'flagged', label: 'Flagged by the scan', count: s.flagged, cls: 'badge-warning', target: 'review' },
    { id: 'injection', label: 'Prompt-injection warnings', count: s.injection, cls: 'badge-warning', target: 'review' },
    { id: 'reports', label: 'Reported by users', count: s.reports, cls: 'badge-warning', target: 'review' },
    { id: 'access', label: 'Access requests', count: review.registrations, cls: 'badge-info', target: 'access' },
  ].filter((r) => r.count > 0)
})

function go(target) {
  // Close the daisyUI dropdown (it stays open while focus is inside it).
  if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
  emit('open', target)
}
</script>
