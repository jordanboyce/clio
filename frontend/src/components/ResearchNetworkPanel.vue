<template>
  <div class="network-panel min-w-0 space-y-5" data-testid="research-network">
    <!-- Reliability: said first, in the server's words, when the numbers are thin -->
    <div
      v-if="showPapers && citation?.reliability && citation.reliability.reliable === false"
      role="note"
      class="reliability"
      data-testid="network-reliability"
    >
      <p class="wrap-anywhere">{{ citation.reliability.note }}</p>
    </div>

    <!-- Key papers -->
    <section v-if="showPapers && citationHasGraph" data-testid="network-key-papers">
      <h4 class="panel-heading">Key papers</h4>
      <p v-if="definitions.betweenness" class="caption">{{ definitions.betweenness }}</p>
      <p v-if="!brokers.length" class="text-xs text-base-content/60">No paper here links separate groups.</p>
      <ul v-else class="space-y-2">
        <li v-for="b in shownBrokers" :key="b.id" class="min-w-0" data-testid="network-broker">
          <button
            type="button"
            class="node-row"
            :title="b.label"
            @click="emit('focus', b.id)"
          >
            <span class="min-w-0 flex-1 line-clamp-2 wrap-anywhere" :class="asCited(b.id) ? 'italic text-base-content/70' : ''">{{ b.label }}</span>
          </button>
          <p class="sentence">{{ b.sentence }}</p>
          <button
            type="button"
            class="disclosure"
            :aria-expanded="isOpen('paper', b.id)"
            @click="toggle('paper', b.id)"
          >
            <ChevronRight :size="12" class="transition-transform shrink-0" :class="isOpen('paper', b.id) ? 'rotate-90' : ''" aria-hidden="true" />
            Show the links
          </button>
          <div v-if="isOpen('paper', b.id)" class="evidence" data-testid="network-broker-links">
            <div v-for="bridge in b.bridges" :key="bridge.community" class="min-w-0">
              <p class="bridge-title wrap-anywhere">
                {{ bridge.label }}
                <span class="tabular-nums font-normal text-base-content/55">· {{ bridge.count }} link{{ bridge.count === 1 ? '' : 's' }}</span>
              </p>
              <ul class="space-y-0.5">
                <li v-for="edge in bridge.edges" :key="edge.node">
                  <button type="button" class="node-row" :title="edge.label" @click="emit('focus', edge.node)">
                    <span class="min-w-0 flex-1">
                      <span class="block line-clamp-2 wrap-anywhere" :class="asCited(edge.node) ? 'italic text-base-content/70' : ''">{{ edge.label }}</span>
                      <span v-if="edge.cited_by" class="block text-[11px] text-base-content/55 wrap-anywhere">cites this · {{ edge.cited_by.reason }}</span>
                      <span v-if="edge.cites" class="block text-[11px] text-base-content/55 wrap-anywhere">cited by this · {{ edge.cites.reason }}</span>
                    </span>
                  </button>
                </li>
              </ul>
              <p v-if="bridge.count > bridge.edges.length" class="text-[11px] text-base-content/55">
                and {{ bridge.count - bridge.edges.length }} more
              </p>
            </div>
            <dl class="metrics">
              <div>
                <dt>Betweenness <span class="tabular-nums">{{ fmt(b.betweenness) }}</span></dt>
                <dd>{{ definitions.betweenness }}</dd>
              </div>
              <div>
                <dt>Constraint <span class="tabular-nums">{{ fmt(b.constraint) }}</span></dt>
                <dd>{{ definitions.constraint }}</dd>
              </div>
            </dl>
          </div>
        </li>
      </ul>
      <button v-if="brokers.length > LIMIT && !showAll.paper" type="button" class="btn btn-ghost btn-xs px-0" @click="showAll.paper = true">
        Show all {{ brokers.length }}
      </button>
      <p v-if="citation.stats?.betweenness_approximate" class="caption mt-1.5">
        Betweenness estimated from a sample of {{ citation.stats.betweenness_samples }}.
      </p>
    </section>

    <!-- Key people and key concepts: lists, not map nodes -->
    <template v-for="list in lists" :key="list.kind">
      <section v-if="list.visible" :data-testid="list.testid">
        <h4 class="panel-heading">{{ list.title }}</h4>
        <p v-if="list.network.reliability?.reliable === false" class="caption wrap-anywhere">{{ list.network.reliability.note }}</p>
        <p v-if="list.kind === 'concept'" class="caption">Concepts are capitalised phrases found by a heuristic, so names can appear among them.</p>
        <p v-if="list.network.definitions?.betweenness" class="caption">{{ list.network.definitions.betweenness }}</p>
        <p v-if="list.network.stats?.capped" class="caption">
          Showing the {{ list.network.stats.capped.kept }} most frequent of {{ list.network.stats.capped.kept + list.network.stats.capped.dropped }}
        </p>
        <p v-if="list.network.stats?.betweenness_approximate" class="caption">
          Betweenness estimated from a sample of {{ list.network.stats.betweenness_samples }}.
        </p>
        <p v-for="w in list.network.warnings || []" :key="w" class="caption wrap-anywhere">{{ w }}</p>

        <ul class="space-y-2.5">
          <li v-for="item in list.rows" :key="item.id" class="min-w-0" data-testid="network-key-node">
            <p class="text-[0.8125rem] leading-snug font-medium wrap-anywhere" :title="item.label">{{ item.label }}</p>
            <p class="sentence">{{ item.sentence }}</p>
            <button
              type="button"
              class="disclosure"
              :aria-expanded="isOpen(list.kind, item.id)"
              @click="toggle(list.kind, item.id)"
            >
              <ChevronRight :size="12" class="transition-transform shrink-0" :class="isOpen(list.kind, item.id) ? 'rotate-90' : ''" aria-hidden="true" />
              Show the evidence
            </button>
            <div v-if="isOpen(list.kind, item.id)" class="evidence" data-testid="network-evidence">
              <div v-if="item.sources?.length" class="min-w-0">
                <p class="bridge-title">In these sources</p>
                <ul class="space-y-0.5">
                  <li v-for="src in item.sources" :key="src.document_id">
                    <button type="button" class="node-row" :title="src.title" @click="emit('open', src.document_id)">
                      <span class="min-w-0 flex-1 line-clamp-2 wrap-anywhere">{{ src.title }}</span>
                    </button>
                  </li>
                </ul>
                <p v-if="item.source_count > item.sources.length" class="text-[11px] text-base-content/55">
                  and {{ item.source_count - item.sources.length }} more
                </p>
              </div>
              <div v-for="bridge in item.bridges || []" :key="bridge.community" class="min-w-0">
                <p class="bridge-title wrap-anywhere">
                  {{ bridge.label }}
                  <span class="tabular-nums font-normal text-base-content/55">· {{ bridge.count }} link{{ bridge.count === 1 ? '' : 's' }}</span>
                </p>
                <ul class="space-y-1">
                  <li v-for="edge in bridge.edges" :key="edge.node" class="text-xs leading-snug wrap-anywhere">
                    {{ edge.label }}
                    <span v-if="edge.sources?.length" class="text-base-content/55"> — together in {{ edge.sources.map(s => s.title).join('; ') }}</span>
                  </li>
                </ul>
                <p v-if="bridge.count > bridge.edges.length" class="text-[11px] text-base-content/55">
                  and {{ bridge.count - bridge.edges.length }} more
                </p>
              </div>
            </div>
          </li>
        </ul>
        <button v-if="list.all.length > LIMIT && !showAll[list.kind]" type="button" class="btn btn-ghost btn-xs px-0" @click="showAll[list.kind] = true">
          Show all {{ list.all.length }}
        </button>

        <!-- Names merged or kept apart: a false merge would invent a broker -->
        <div v-if="list.kind === 'person' && nameCare" class="mt-2 space-y-1" data-testid="network-name-care">
          <template v-if="merges.length">
            <button type="button" class="disclosure" :aria-expanded="isOpen('care', 'merges')" @click="toggle('care', 'merges')">
              <ChevronRight :size="12" class="transition-transform shrink-0" :class="isOpen('care', 'merges') ? 'rotate-90' : ''" aria-hidden="true" />
              {{ merges.length }} name{{ merges.length === 1 ? '' : 's' }} merged
            </button>
            <ul v-if="isOpen('care', 'merges')" class="evidence space-y-1">
              <li v-for="m in merges" :key="m.name" class="text-xs leading-snug wrap-anywhere">
                <span class="font-medium">{{ m.name }}</span>: {{ m.variants.join(', ') }}
                <span class="text-base-content/55"> — {{ m.reason }}</span>
              </li>
            </ul>
          </template>
          <template v-if="ambiguous.length">
            <button type="button" class="disclosure" :aria-expanded="isOpen('care', 'ambiguous')" @click="toggle('care', 'ambiguous')">
              <ChevronRight :size="12" class="transition-transform shrink-0" :class="isOpen('care', 'ambiguous') ? 'rotate-90' : ''" aria-hidden="true" />
              {{ ambiguous.length }} left apart
            </button>
            <ul v-if="isOpen('care', 'ambiguous')" class="evidence space-y-1">
              <li v-for="(a, i) in ambiguous" :key="i" class="text-xs leading-snug wrap-anywhere">
                {{ a.names.join(', ') }}
                <span class="text-base-content/55"> — {{ a.reason }}</span>
              </li>
            </ul>
          </template>
          <template v-if="unparsed.length">
            <button type="button" class="disclosure" :aria-expanded="isOpen('care', 'unparsed')" @click="toggle('care', 'unparsed')">
              <ChevronRight :size="12" class="transition-transform shrink-0" :class="isOpen('care', 'unparsed') ? 'rotate-90' : ''" aria-hidden="true" />
              {{ unparsed.length }} author string{{ unparsed.length === 1 ? '' : 's' }} could not be read
            </button>
            <ul v-if="isOpen('care', 'unparsed')" class="evidence space-y-1">
              <li v-for="(u, i) in unparsed" :key="i" class="text-xs leading-snug wrap-anywhere">{{ u }}</li>
            </ul>
          </template>
        </div>
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed, reactive } from 'vue'
import { ChevronRight } from 'lucide-vue-next'
import { isAsCited } from '../utils/citationGraph.js'

const props = defineProps({
  citation: { type: Object, default: null },
  authors: { type: Object, default: null },
  entities: { type: Object, default: null },
  // The citation graph's nodes by id, to show references as printed in italics.
  nodeById: { type: Object, default: () => ({}) },
  // 'papers' | 'people' | 'concepts' | 'all'
  section: { type: String, default: 'all' },
})
const emit = defineEmits(['focus', 'open'])

const LIMIT = 5

const showPapers = computed(() => props.section === 'all' || props.section === 'papers')
const showPeople = computed(() => props.section === 'all' || props.section === 'people')
const showConcepts = computed(() => props.section === 'all' || props.section === 'concepts')

const definitions = computed(() => props.citation?.definitions || {})
const brokers = computed(() => props.citation?.brokers || [])
const citationHasGraph = computed(() => Boolean(props.citation && (props.citation.stats?.edges > 0 || brokers.value.length)))

const showAll = reactive({ paper: false, person: false, concept: false })
const shownBrokers = computed(() => (showAll.paper ? brokers.value : brokers.value.slice(0, LIMIT)))
const openRows = reactive({})
const isOpen = (kind, id) => Boolean(openRows[`${kind}:${id}`])
const toggle = (kind, id) => { openRows[`${kind}:${id}`] = !openRows[`${kind}:${id}`] }

const asCited = (id) => isAsCited(props.nodeById?.[id])
const fmt = (v) => (Number.isFinite(Number(v)) && v !== null ? Number(v).toFixed(2) : '—')

function listFor(kind, network, title, testid, visible) {
  const all = network?.key_nodes || []
  return {
    kind, title, testid, network: network || {}, all,
    rows: showAll[kind] ? all : all.slice(0, LIMIT),
    visible: Boolean(visible && network && all.length),
  }
}
const lists = computed(() => [
  listFor('person', props.authors, 'Key people', 'network-key-people', showPeople.value),
  listFor('concept', props.entities, 'Key concepts', 'network-key-concepts', showConcepts.value),
])

const merges = computed(() => props.authors?.merges || [])
const ambiguous = computed(() => props.authors?.ambiguous || [])
const unparsed = computed(() => props.authors?.unparsed_authors || [])
const nameCare = computed(() => merges.value.length || ambiguous.value.length || unparsed.value.length)
</script>

<style scoped>
.wrap-anywhere { overflow-wrap: anywhere; word-break: normal; }
.panel-heading { margin-bottom: 0.375rem; font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.025em; color: color-mix(in oklch, var(--color-base-content) 55%, transparent); }
.node-row { display: flex; width: 100%; min-width: 0; align-items: flex-start; gap: 0.5rem; border-radius: 0.375rem; padding: 0.25rem 0.375rem; margin-inline: -0.375rem; text-align: left; font-size: 0.8125rem; line-height: 1.35; }
.node-row:hover, .node-row:focus-visible { background: color-mix(in oklch, var(--color-base-content) 7%, transparent); }
.caption { margin-bottom: 0.375rem; font-size: 0.6875rem; line-height: 1.4; overflow-wrap: anywhere; color: color-mix(in oklch, var(--color-base-content) 55%, transparent); }
.sentence { font-size: 0.75rem; line-height: 1.4; overflow-wrap: anywhere; color: color-mix(in oklch, var(--color-base-content) 70%, transparent); }
.reliability { border: 1px solid color-mix(in oklch, var(--color-warning) 45%, transparent); background: color-mix(in oklch, var(--color-warning) 12%, var(--color-base-100)); border-radius: var(--radius-box, 0.5rem); padding: 0.625rem 0.75rem; font-size: 0.75rem; line-height: 1.45; color: var(--color-base-content); }
.disclosure { display: inline-flex; max-width: 100%; align-items: center; gap: 0.25rem; margin-top: 0.125rem; padding: 0.125rem 0; font-size: 0.6875rem; text-align: left; color: color-mix(in oklch, var(--color-base-content) 65%, transparent); }
.disclosure:hover, .disclosure:focus-visible { color: var(--color-base-content); text-decoration: underline; }
.evidence { margin-top: 0.25rem; min-width: 0; border-left: 2px solid var(--color-base-300); padding-left: 0.625rem; display: flex; flex-direction: column; gap: 0.5rem; }
.bridge-title { font-size: 0.6875rem; font-weight: 600; line-height: 1.35; color: color-mix(in oklch, var(--color-base-content) 75%, transparent); }
.metrics { display: flex; flex-direction: column; gap: 0.375rem; font-size: 0.6875rem; line-height: 1.4; color: color-mix(in oklch, var(--color-base-content) 60%, transparent); }
.metrics dt { font-weight: 600; color: color-mix(in oklch, var(--color-base-content) 75%, transparent); }
.metrics dd { overflow-wrap: anywhere; }
</style>
