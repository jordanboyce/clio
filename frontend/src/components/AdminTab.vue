<template>
  <div class="max-w-4xl mx-auto space-y-8">
    <header class="pb-4 border-b border-base-300/60">
      <h1 class="text-[22px] leading-none font-semibold tracking-tight">Administration</h1>
      <p class="mt-2 text-xs text-base-content/50">
        What needs your decision, who is using this deployment, what it's spending, and who can reach it.
      </p>
    </header>

    <!-- ═══ Needs review ═══
         First on the page: it is the one part of Admin that is a to-do list.
         One card per document, every reason attached, most urgent first. -->
    <section aria-labelledby="admin-review">
      <div class="flex items-center justify-between mb-3 gap-2 flex-wrap">
        <h2 id="admin-review" class="text-sm font-semibold uppercase tracking-wider text-base-content/60 flex items-center gap-2">
          Needs review
          <span v-if="reviewQueue.length" class="badge badge-sm badge-warning normal-case tracking-normal font-medium tabular-nums">{{ reviewQueue.length }}</span>
        </h2>
        <span class="text-xs text-base-content/45">
          scanner: <span class="font-mono">{{ review.content_policy_action || '…' }}</span>
          · injection: <span class="font-mono">{{ review.injection_scan_action || '…' }}</span>
          · personal data: <span class="font-mono">{{ review.pii_scan_action || '…' }}</span>
          · {{ review.blocked_hashes ?? 0 }} blocked hash{{ (review.blocked_hashes ?? 0) === 1 ? '' : 'es' }}
        </span>
      </div>

      <div v-if="reviewError" class="alert alert-error py-2">
        <span class="text-sm">{{ reviewError }}</span>
        <button class="btn btn-xs btn-ghost" @click="loadReview">Retry</button>
      </div>

      <div v-else-if="reviewLoading && reviewQueue.length === 0" class="text-center py-8 text-sm text-base-content/50 border border-dashed border-base-300 rounded-lg">
        Checking what needs a decision…
      </div>

      <template v-else>
        <div v-if="reviewQueue.length === 0" class="text-center py-8 text-sm text-base-content/50 border border-dashed border-base-300 rounded-lg">
          <ShieldCheck :size="20" class="mx-auto mb-2 text-success" aria-hidden="true" />
          All clear. Nothing is held, flagged or reported.
        </div>

        <template v-else>
          <div class="flex flex-wrap items-center gap-1.5 mb-3" role="group" aria-label="Filter the review queue">
            <button
              v-for="f in REVIEW_FILTERS"
              :key="f.id"
              v-show="f.id === 'all' || filterCount(f.id) > 0"
              class="btn btn-xs rounded-full normal-case font-normal"
              :class="reviewFilter === f.id ? 'btn-neutral' : 'btn-ghost border border-base-300'"
              :aria-pressed="reviewFilter === f.id"
              @click="reviewFilter = f.id"
            >{{ f.label }} <span class="tabular-nums opacity-70">{{ filterCount(f.id) }}</span></button>
            <label v-if="visibleQueue.length > 1" class="ml-auto flex items-center gap-1.5 text-xs cursor-pointer select-none">
              <input
                type="checkbox"
                class="checkbox checkbox-xs"
                :checked="allShownSelected"
                :indeterminate.prop="selectedShown.length > 0 && !allShownSelected"
                aria-label="Select every document shown"
                data-testid="select-all-shown"
                @change="toggleSelectAllShown"
              />
              Select all shown
            </label>
            <button
              v-if="visibleQueue.length > 1"
              class="btn btn-xs btn-ghost text-success"
              :disabled="bulkBusy"
              title="Marks every document the current filter shows as reviewed and fine — same as clicking its green button on each."
              @click="approveAll"
            >
              <span v-if="bulkBusy === 'approve-all'" class="loading loading-spinner loading-xs"></span>
              Approve all {{ visibleQueue.length }} shown
            </button>
          </div>

          <!-- Bulk decisions for the checked rows: approve (keep) or remove (deny). -->
          <div
            v-if="selectedShown.length"
            class="mb-3 rounded-lg border border-primary/40 bg-primary/5 px-3 py-2 flex flex-wrap items-center gap-2 text-xs"
            role="toolbar"
            aria-label="Bulk actions for the selected documents"
            data-testid="bulk-bar"
          >
            <span class="font-medium tabular-nums">{{ selectedShown.length }} selected</span>
            <button class="btn btn-xs btn-success btn-outline" :disabled="!!bulkBusy" data-testid="bulk-approve" @click="approveSelected">
              <span v-if="bulkBusy === 'approve'" class="loading loading-spinner loading-xs"></span>
              Approve selected
            </button>
            <button class="btn btn-xs btn-error btn-outline" :disabled="!!bulkBusy" data-testid="bulk-remove-block" @click="removeSelected(true)">
              <span v-if="bulkBusy === 'remove-block'" class="loading loading-spinner loading-xs"></span>
              Remove &amp; block selected
            </button>
            <button class="btn btn-xs btn-ghost" :disabled="!!bulkBusy" data-testid="bulk-remove" @click="removeSelected(false)">
              <span v-if="bulkBusy === 'remove'" class="loading loading-spinner loading-xs"></span>
              Remove selected
            </button>
            <button class="btn btn-xs btn-ghost ml-auto" @click="clearSelection">Clear</button>
          </div>

          <ul class="space-y-2">
            <li
              v-for="d in visibleQueue"
              :key="key(d)"
              class="rounded-lg border bg-base-100"
              :class="d.priority === 'critical' ? 'border-error/60' : 'border-base-300/60'"
            >
              <div class="p-3 flex flex-wrap items-start gap-x-4 gap-y-2">
                <input
                  type="checkbox"
                  class="checkbox checkbox-sm mt-0.5"
                  :checked="selected.has(key(d))"
                  :aria-label="`Select ${d.filename}`"
                  data-testid="row-select"
                  @change="toggleSelected(d)"
                />
                <div class="min-w-0 flex-1 basis-64">
                  <div class="flex items-center gap-2 flex-wrap">
                    <span class="badge badge-sm" :class="priorityBadge(d.priority).cls">{{ priorityBadge(d.priority).text }}</span>
                    <span v-if="policyBadge(d.policy_status) && d.policy_status === 'quarantined'" class="badge badge-sm badge-error badge-outline" :title="policyBadge(d.policy_status).title">hidden from search</span>
                    <span class="font-medium truncate max-w-[40ch]" :title="d.filename">{{ d.filename }}</span>
                  </div>
                  <div class="text-[11px] text-base-content/50 mt-0.5">
                    {{ d.collection_name }}<template v-if="d.uploaded_by"> · added by {{ d.uploaded_by }}</template> · {{ formatDay(d.upload_timestamp) }}
                  </div>
                  <ul class="mt-2 space-y-1">
                    <li v-for="r in d.reasons" :key="r.kind" class="text-xs flex items-start gap-2">
                      <span class="badge badge-xs badge-ghost flex-shrink-0 mt-0.5">{{ KIND_LABELS[r.kind] || r.kind }}</span>
                      <span class="text-base-content/75">{{ r.text }}</span>
                    </li>
                  </ul>
                </div>
                <div class="flex items-center gap-1 flex-wrap justify-end">
                  <button class="btn btn-xs btn-ghost" :aria-expanded="expanded === key(d)" @click="toggleDetails(d)">{{ expanded === key(d) ? 'Hide evidence' : 'Evidence' }}</button>
                  <button
                    class="btn btn-xs btn-success btn-outline"
                    :disabled="busy === key(d)"
                    :title="approveHint(d)"
                    @click="approve(d)"
                  >{{ approveLabel(d) }}</button>
                  <button class="btn btn-xs btn-error btn-outline" :disabled="busy === key(d)" @click="remove(d, true)">Remove &amp; block</button>
                </div>
              </div>

              <div v-if="expanded === key(d)" class="border-t border-base-300/60 bg-base-200/50 p-3 space-y-3 text-xs">
                <div v-if="d.kinds.includes('policy')">
                  <div class="font-semibold mb-1">Content scan</div>
                  <div v-if="!policyFlagPages(d.policy_flags).length" class="text-base-content/50">No page-level findings recorded.</div>
                  <div v-for="entry in policyFlagPages(d.policy_flags)" :key="entry.page" class="mb-2">
                    <div class="text-base-content/70">Page {{ entry.page }} · score {{ entry.risk_score }}</div>
                    <div v-for="(f, i) in entry.findings" :key="i" class="ml-2 mt-0.5">
                      <span class="capitalize">{{ categoryLabel(f.category) }}</span>
                      <span class="badge badge-xs ml-1" :class="f.severity === 'critical' || f.severity === 'high' ? 'badge-error' : 'badge-warning'">{{ f.severity }}</span>
                      <code v-if="f.matched_text" class="ml-1 text-[11px] text-base-content/70 break-all">{{ f.matched_text }}</code>
                    </div>
                  </div>
                  <div v-if="d.policy_flags?.llm?.rationale" class="text-base-content/60">
                    Model opinion: {{ d.policy_flags.llm.rationale }}
                  </div>
                </div>

                <div v-if="d.kinds.includes('injection')">
                  <div class="font-semibold mb-1">
                    Prompt injection
                    <span class="badge badge-xs ml-1" :class="d.injection.level === 'high' ? 'badge-error' : 'badge-warning'">{{ d.injection.level }}</span>
                    <span class="font-normal text-base-content/55 ml-1">on {{ d.injection.flagged_pages }} page{{ d.injection.flagged_pages === 1 ? '' : 's' }} · score {{ d.injection.max_score }}</span>
                  </div>
                  <div v-for="(t, i) in injectionEvidence(d.injection)" :key="i" class="ml-2 mt-0.5 flex flex-wrap items-center gap-x-1 gap-y-0.5">
                    <span class="capitalize">{{ t.category }}</span>
                    <span class="text-base-content/45"> · p.{{ t.page }}</span>
                    <span class="badge badge-xs ml-1" :class="t.severity === 'high' ? 'badge-error' : 'badge-warning'">{{ t.severity }}</span>
                    <code class="ml-1 text-[11px] text-base-content/70 break-all">{{ t.matched_text }}</code>
                    <template v-if="t.pattern_name">
                      <button
                        class="btn btn-ghost btn-xs h-5 min-h-0 px-1.5 text-[11px]"
                        :title="`Stop the rule '${t.pattern_name}' from flagging anything, everywhere. Reversible under Scanner tuning.`"
                        data-testid="mute-pattern"
                        @click="tune('mute', { pattern: t.pattern_name })"
                      >Mute rule</button>
                      <button
                        v-if="t.matched_text"
                        class="btn btn-ghost btn-xs h-5 min-h-0 px-1.5 text-[11px]"
                        title="Never flag this exact wording again, in any document. Reversible under Scanner tuning."
                        data-testid="allow-wording"
                        @click="tune('allow', { phrase: t.matched_text })"
                      >Allow wording</button>
                    </template>
                  </div>
                  <p class="mt-1 text-base-content/50">
                    Text like this can steer an AI that reads the document. Approving keeps the warning on the
                    source but takes it off this list; it does not hide the document. Removing it counts as a
                    confirmed hit for the rules above; approving counts as a miss — rules that are usually
                    wrong are turned down automatically (see Scanner tuning below).
                  </p>
                </div>

                <div v-if="d.kinds.includes('report')">
                  <div class="font-semibold mb-1">
                    Reports
                    <span class="font-normal text-base-content/55 ml-1">{{ d.reports.count }} from {{ d.reports.reporters.join(', ') }} · latest {{ formatWhen(d.reports.last_at) }}</span>
                  </div>
                  <div v-if="!d.reports.reasons.length" class="text-base-content/50">No reason was given.</div>
                  <ul v-else class="list-disc ml-5 space-y-0.5">
                    <li v-for="(reason, i) in d.reports.reasons" :key="i" class="text-base-content/75">{{ reason }}</li>
                  </ul>
                </div>

                <button class="btn btn-xs btn-ghost" :disabled="busy === key(d)" @click="remove(d, false)">Remove without blocking</button>
              </div>
            </li>
          </ul>
        </template>
      </template>
    </section>

    <!-- ═══ Scanner tuning ═══
         Why the queue keeps filling, and the levers to stop it: muted rules,
         allowed wordings, and what reviewers' decisions have taught the scanner. -->
    <section aria-labelledby="admin-tuning">
      <details class="rounded-lg border border-base-300/60 bg-base-100" @toggle="onTuningToggle">
        <summary class="cursor-pointer px-4 py-3 flex items-center gap-2 flex-wrap">
          <h2 id="admin-tuning" class="text-sm font-semibold uppercase tracking-wider text-base-content/60">Scanner tuning</h2>
          <span class="text-xs text-base-content/45">
            prompt injection ·
            {{ (review.injection_tuning?.muted || []).length }} muted ·
            {{ (review.injection_tuning?.allow_phrases || []).length }} allowed wording{{ (review.injection_tuning?.allow_phrases || []).length === 1 ? '' : 's' }} ·
            {{ (review.injection_tuning?.noisy || []).length }} turned down
          </span>
        </summary>
        <div class="px-4 pb-4 space-y-4 text-xs">
          <p class="text-base-content/60">
            The prompt-injection scan is wording-based. Every approve counts as a miss and every remove as a hit
            for the rules that flagged the document; a rule dismissed at least {{ tuning.noisy_rule?.min_dismissals ?? 5 }} times and
            right less than {{ Math.round((1 - (tuning.noisy_rule?.dismiss_ratio ?? 0.8)) * 100) }}% of the time is scanned one level
            lower until you reset it. Muting a rule or allowing a wording applies everywhere at once, including
            to warnings already on sources, and can be undone here.
          </p>

          <div v-if="tuningError" class="alert alert-error py-2"><span>{{ tuningError }}</span></div>
          <div v-else-if="tuningLoading" class="text-base-content/50">Loading…</div>
          <template v-else>
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <div class="space-y-2">
                <div class="font-medium">Allowed wordings</div>
                <div class="flex gap-2">
                  <input
                    v-model="newAllowPhrase"
                    class="input input-bordered input-xs flex-1"
                    placeholder="e.g. act as a fiduciary"
                    aria-label="Wording to allow"
                    data-testid="allow-input"
                    @keyup.enter="addAllowPhrase"
                  />
                  <button class="btn btn-xs btn-outline" :disabled="newAllowPhrase.trim().length < 4 || tuningBusy" @click="addAllowPhrase">Allow</button>
                </div>
                <div v-if="!tuning.allow_phrases?.length" class="text-base-content/45">None. A match that contains an allowed wording (or sits inside it) is ignored.</div>
                <ul v-else class="space-y-1 max-h-40 overflow-y-auto">
                  <li v-for="p in tuning.allow_phrases" :key="p" class="flex items-center gap-2">
                    <code class="text-[11px] truncate flex-1" :title="p">{{ p }}</code>
                    <button class="btn btn-ghost btn-xs" :disabled="tuningBusy" @click="tune('disallow', { phrase: p })">Remove</button>
                  </li>
                </ul>
              </div>
              <div class="space-y-2">
                <div class="font-medium">Muted rules</div>
                <div v-if="!tuning.muted?.length" class="text-base-content/45">None. Mute a rule from a document's evidence, or from the table below.</div>
                <ul v-else class="flex flex-wrap gap-1.5">
                  <li v-for="m in tuning.muted" :key="m">
                    <button class="badge badge-sm badge-ghost gap-1 font-mono" :disabled="tuningBusy" :title="`Unmute ${m}`" @click="tune('unmute', { pattern: m })">{{ m }} ✕</button>
                  </li>
                </ul>
                <div class="font-medium pt-2">Turned down by feedback</div>
                <div v-if="!tuning.noisy?.length" class="text-base-content/45">None yet.</div>
                <ul v-else class="flex flex-wrap gap-1.5">
                  <li v-for="m in tuning.noisy" :key="m">
                    <button class="badge badge-sm badge-warning badge-outline gap-1 font-mono" :disabled="tuningBusy" :title="`Reset the statistics for ${m}`" @click="tune('reset_feedback', { pattern: m })">{{ m }} ↺</button>
                  </li>
                </ul>
              </div>
            </div>

            <div class="overflow-x-auto rounded-lg border border-base-300/60 max-h-80">
              <table class="table table-xs" data-testid="tuning-table">
                <thead>
                  <tr class="text-[10px] uppercase tracking-wider text-base-content/45">
                    <th>Rule</th>
                    <th>Category</th>
                    <th>Severity</th>
                    <th class="text-right">Misses</th>
                    <th class="text-right">Hits</th>
                    <th>State</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="p in tuning.patterns" :key="p.name">
                    <td class="font-mono">{{ p.name }}</td>
                    <td class="capitalize">{{ categoryLabel(p.category) }}</td>
                    <td>{{ p.severity }}</td>
                    <td class="text-right tabular-nums">{{ p.dismissed }}</td>
                    <td class="text-right tabular-nums">{{ p.confirmed }}</td>
                    <td>
                      <span v-if="p.muted" class="badge badge-xs badge-ghost">muted</span>
                      <span v-else-if="p.noisy" class="badge badge-xs badge-warning badge-outline">turned down</span>
                      <span v-else class="text-base-content/40">—</span>
                    </td>
                    <td class="text-right whitespace-nowrap">
                      <button v-if="p.muted" class="btn btn-ghost btn-xs" :disabled="tuningBusy" @click="tune('unmute', { pattern: p.name })">Unmute</button>
                      <button v-else class="btn btn-ghost btn-xs" :disabled="tuningBusy" @click="tune('mute', { pattern: p.name })">Mute</button>
                      <button v-if="p.dismissed || p.confirmed" class="btn btn-ghost btn-xs" :disabled="tuningBusy" @click="tune('reset_feedback', { pattern: p.name })">Reset</button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </template>
        </div>
      </details>
    </section>

    <!-- ═══ Live system card ═══ -->
    <section aria-labelledby="admin-system">
      <div class="flex items-center justify-between mb-3">
        <h2 id="admin-system" class="text-sm font-semibold uppercase tracking-wider text-base-content/60">System</h2>
        <button class="btn btn-ghost btn-xs gap-1" @click="loadAll" :disabled="loading">
          <RefreshCw :size="12" :class="{ 'animate-spin': loading }" aria-hidden="true" />
          Refresh
        </button>
      </div>

      <div v-if="statsError" class="alert alert-error py-2">
        <span class="text-sm">{{ statsError }}</span>
        <button class="btn btn-xs btn-ghost" @click="loadAll">Retry</button>
      </div>

      <div v-else class="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div class="rounded-lg border border-base-300/60 bg-base-100 p-4">
          <div class="text-[10px] uppercase tracking-wider text-base-content/45">In flight</div>
          <div class="mt-1 text-xl font-semibold tabular-nums">{{ sys.in_flight ?? '—' }}</div>
          <div class="text-[11px] text-base-content/45">requests right now</div>
        </div>
        <div class="rounded-lg border border-base-300/60 bg-base-100 p-4">
          <div class="text-[10px] uppercase tracking-wider text-base-content/45">Requests</div>
          <div class="mt-1 text-xl font-semibold tabular-nums">{{ formatNum(sys.requests_total) }}</div>
          <div class="text-[11px] text-base-content/45">
            since start · {{ formatNum(sys.errors_total) }} errors
          </div>
        </div>
        <div class="rounded-lg border border-base-300/60 bg-base-100 p-4">
          <div class="text-[10px] uppercase tracking-wider text-base-content/45">Rate limited</div>
          <div class="mt-1 text-xl font-semibold tabular-nums">{{ rateLimitRejected }}</div>
          <div class="text-[11px] text-base-content/45">
            {{ sys.rate_limit?.enabled ? 'requests rejected' : 'limiter disabled' }}
          </div>
        </div>
        <div class="rounded-lg border border-base-300/60 bg-base-100 p-4">
          <div class="text-[10px] uppercase tracking-wider text-base-content/45">Index jobs</div>
          <div class="mt-1 text-xl font-semibold tabular-nums">
            {{ sys.index_jobs_active ?? 0 }}<span class="text-sm text-base-content/40">/{{ sys.max_concurrent_index_jobs ?? '∞' }}</span>
          </div>
          <div class="text-[11px] text-base-content/45">uptime {{ formatUptime(sys.uptime_seconds) }}</div>
        </div>
      </div>

      <div v-if="sys.usage_today" class="mt-3 rounded-lg border border-base-300/60 bg-base-100 p-4 flex flex-wrap items-baseline gap-x-6 gap-y-1">
        <span class="text-[10px] uppercase tracking-wider text-base-content/45">Today</span>
        <span class="text-sm tabular-nums"><strong>{{ formatNum(sys.usage_today.turns) }}</strong> chat turns</span>
        <span class="text-sm tabular-nums"><strong>{{ formatNum(totalTokens(sys.usage_today)) }}</strong> tokens</span>
        <span class="text-sm tabular-nums"><strong>{{ formatNum(sys.usage_today.cache_hits) }}</strong> cache hits</span>
        <span v-if="sys.daily_token_budget" class="text-sm text-base-content/55 tabular-nums">
          budget {{ formatNum(sys.daily_token_budget) }}/user/day
        </span>
        <span v-else class="text-sm text-base-content/45">no daily budget set</span>
      </div>
    </section>

    <!-- ═══ Usage by person ═══ -->
    <section aria-labelledby="admin-usage">
      <div class="flex items-center justify-between mb-3">
        <h2 id="admin-usage" class="text-sm font-semibold uppercase tracking-wider text-base-content/60">Usage — last {{ usageDays }} days</h2>
        <div class="join" role="group" aria-label="Usage window">
          <button
            v-for="d in [7, 30, 90]"
            :key="d"
            class="btn btn-xs join-item"
            :class="usageDays === d ? 'btn-active' : 'btn-ghost'"
            @click="usageDays = d; loadUsage()"
            :aria-pressed="usageDays === d"
          >{{ d }}d</button>
        </div>
      </div>

      <div v-if="usageError" class="alert alert-error py-2">
        <span class="text-sm">{{ usageError }}</span>
        <button class="btn btn-xs btn-ghost" @click="loadUsage">Retry</button>
      </div>

      <div v-else-if="byUser.length === 0" class="text-center py-10 text-sm text-base-content/45 border border-dashed border-base-300 rounded-lg">
        No chat activity recorded in this window yet.
      </div>

      <div v-else class="overflow-x-auto rounded-lg border border-base-300/60">
        <table class="table table-sm">
          <thead>
            <tr class="text-[10px] uppercase tracking-wider text-base-content/45">
              <th>Person</th>
              <th class="text-right">Turns</th>
              <th class="text-right">Tokens in</th>
              <th class="text-right">Tokens out</th>
              <th class="text-right">Cache hits</th>
              <th class="text-right">Last active</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in byUser" :key="row.grouped_by">
              <td class="font-medium truncate max-w-[22ch]">{{ row.grouped_by }}</td>
              <td class="text-right tabular-nums">{{ formatNum(row.turns) }}</td>
              <td class="text-right tabular-nums">{{ formatNum(row.input_tokens) }}</td>
              <td class="text-right tabular-nums">{{ formatNum(row.output_tokens) }}</td>
              <td class="text-right tabular-nums">
                {{ formatNum(row.cache_hits) }}
                <span class="text-base-content/40">({{ cacheHitRate(row) }})</span>
              </td>
              <td class="text-right text-base-content/55 whitespace-nowrap">{{ formatDay(row.last_active) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Daily series as compact bars: enough to spot a spike, no chart lib -->
      <div v-if="byDay.length > 1" class="mt-3 rounded-lg border border-base-300/60 bg-base-100 p-4">
        <div class="text-[10px] uppercase tracking-wider text-base-content/45 mb-2">Tokens per day</div>
        <div class="flex items-end gap-[3px] h-16" role="img" :aria-label="`Daily token usage over ${byDay.length} days`">
          <div
            v-for="day in byDay"
            :key="day.grouped_by"
            class="flex-1 bg-primary/70 rounded-t-sm min-w-[3px]"
            :style="{ height: dayBarHeight(day) }"
            :title="`${day.grouped_by}: ${formatNum(totalTokens(day))} tokens, ${formatNum(day.turns)} turns`"
          ></div>
        </div>
      </div>
    </section>

    <!-- ═══ Policy settings ═══ -->
    <section aria-labelledby="admin-policy">
      <h2 id="admin-policy" class="text-sm font-semibold uppercase tracking-wider text-base-content/60 mb-3">Policy</h2>
      <div class="rounded-lg border border-base-300/60 bg-base-100 p-4 space-y-4">
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div class="form-control">
            <label class="label pb-1" for="policy-action"><span class="label-text font-medium">When the scan flags a source</span></label>
            <select id="policy-action" v-model="policyForm.content_policy_action" class="select select-bordered select-sm">
              <option value="off">Off — do not scan</option>
              <option value="flag">Flag — index it, show a badge</option>
              <option value="quarantine">Quarantine — index it, hide it until approved</option>
              <option value="reject">Reject — refuse to index it</option>
            </select>
            <p class="text-[11px] text-base-content/50 mt-1">
              Critical findings (child abuse material indicators, attack planning) are always held, even on Flag.
            </p>
          </div>
          <div class="form-control">
            <label class="label pb-1" for="injection-action"><span class="label-text font-medium">Prompt-injection text in sources</span></label>
            <select id="injection-action" v-model="policyForm.injection_scan_action" class="select select-bordered select-sm">
              <option value="review">Scan — badge the source and queue it here</option>
              <option value="warn">Scan — badge the source only, skip the queue</option>
              <option value="off">Off — do not scan</option>
            </select>
            <p class="text-[11px] text-base-content/50 mt-1">
              Wording-based, so ordinary prose can trip it. "Badge only" keeps the warning on the source without making it your to-do.
            </p>
          </div>
          <div class="form-control">
            <label class="label pb-1" for="pii-action"><span class="label-text font-medium">Personal data in sources</span></label>
            <select id="pii-action" v-model="policyForm.pii_scan_action" class="select select-bordered select-sm" data-testid="pii-action">
              <option value="label">Label — default the source to confidential, no queue</option>
              <option value="flag">Flag — label it and queue it here</option>
              <option value="quarantine">Quarantine — hide it until approved</option>
              <option value="off">Off — do not scan</option>
            </select>
            <div class="flex items-center gap-2 mt-2">
              <span class="text-xs">Counts as personal data from</span>
              <input v-model.number="policyForm.pii_flag_threshold" type="number" min="1" max="1000" class="input input-bordered input-xs w-20" aria-label="Personal-data threshold per page" />
              <span class="text-[11px] text-base-content/45">items on one page</span>
            </div>
            <p class="text-[11px] text-base-content/50 mt-1">
              <template v-if="review.pii_engine?.engine === 'presidio'">
                Microsoft Presidio<template v-if="review.pii_engine?.model"> ({{ review.pii_engine.model }})</template>: names, addresses, ids, bank and card numbers, emails, phones.
              </template>
              <template v-else>
                Built-in patterns: emails, phones, card numbers, IBANs, SSNs, IP addresses. Install Presidio
                (<code>requirements-pii.txt</code>) for names, addresses and national ids.
                <template v-if="review.pii_engine?.error"> Presidio failed to load: {{ review.pii_engine.error }}</template>
              </template>
              Only counts and masked previews are stored, never the data.
            </p>
          </div>
          <div class="form-control">
            <label class="label pb-1 cursor-pointer justify-start gap-2">
              <input v-model="policyForm.pii_redact_outputs" type="checkbox" class="checkbox checkbox-sm" data-testid="pii-redact" />
              <span class="label-text font-medium">Mask personal data in results</span>
            </label>
            <p class="text-[11px] text-base-content/50">
              Replaces each item with its type (&lt;EMAIL_ADDRESS&gt;) in search results, chat context and sources, MCP excerpts and chunk views. The original file is unchanged.
            </p>
            <input v-model="policyForm.pii_entities" class="input input-bordered input-xs mt-2" placeholder="entity types (blank = default set, e.g. add PERSON,LOCATION)" aria-label="Personal-data entity types" />
          </div>
          <div class="form-control">
            <label class="label pb-1 cursor-pointer justify-start gap-2">
              <input v-model="policyForm.content_policy_llm_review" type="checkbox" class="checkbox checkbox-sm" />
              <span class="label-text font-medium">Ask the chat LLM for a second opinion</span>
            </label>
            <p class="text-[11px] text-base-content/50">
              Sends a sample of pages to the provider chat already uses. Costs tokens per upload; can escalate, never clears.
            </p>
            <div v-if="policyForm.content_policy_llm_review" class="flex gap-2 mt-2">
              <input v-model="policyForm.content_policy_llm_provider" class="input input-bordered input-xs flex-1" placeholder="provider (blank = chat default)" aria-label="Review provider" />
              <input v-model="policyForm.content_policy_llm_model" class="input input-bordered input-xs flex-1" placeholder="model (optional)" aria-label="Review model" />
            </div>
          </div>
        </div>

        <div class="border-t border-base-300/60 pt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
          <div class="form-control">
            <label class="label pb-1 cursor-pointer justify-start gap-2">
              <input v-model="policyForm.aup_required" type="checkbox" class="checkbox checkbox-sm" />
              <span class="label-text font-medium">Require acceptance of an acceptable-use policy</span>
            </label>
            <p class="text-[11px] text-base-content/50">
              Each person must accept it (once per version) before adding sources. Needs identities, so it only applies under private collections.
            </p>
            <div class="flex items-center gap-2 mt-2">
              <span class="text-xs">Version</span>
              <input v-model="policyForm.aup_version" class="input input-bordered input-xs w-20" aria-label="Policy version" />
              <span class="text-[11px] text-base-content/45">bump it to re-prompt everyone</span>
            </div>
          </div>
          <div class="form-control">
            <label class="label pb-1" for="policy-text"><span class="label-text font-medium">Policy text (markdown; blank = built-in)</span></label>
            <textarea id="policy-text" v-model="policyForm.aup_text" class="textarea textarea-bordered text-xs font-mono" rows="6" placeholder="Leave empty to use the default policy"></textarea>
          </div>
        </div>

        <div class="flex items-center justify-end gap-2">
          <span v-if="policySaved" class="text-xs text-success">Saved</span>
          <button class="btn btn-sm btn-primary" :disabled="policySaving" @click="savePolicy">
            <span v-if="policySaving" class="loading loading-spinner loading-xs"></span>
            Save policy settings
          </button>
        </div>
      </div>
    </section>

    <!-- ═══ Audit trail ═══ -->
    <section aria-labelledby="admin-audit">
      <div class="flex items-center justify-between mb-3 gap-2 flex-wrap">
        <h2 id="admin-audit" class="text-sm font-semibold uppercase tracking-wider text-base-content/60">Audit trail</h2>
        <div class="flex items-center gap-2">
          <select v-model="auditFilter.action" class="select select-bordered select-xs" aria-label="Filter by action" @change="loadAudit">
            <option value="">All actions</option>
            <option v-for="a in auditActions" :key="a" :value="a">{{ a }}</option>
          </select>
          <input v-model="auditFilter.actor" class="input input-bordered input-xs w-40" placeholder="actor" aria-label="Filter by actor" @keyup.enter="loadAudit" />
          <button class="btn btn-xs btn-ghost" @click="loadAudit">Apply</button>
          <a class="btn btn-xs btn-outline" :href="auditCsvHref" target="_blank" rel="noopener">Export CSV</a>
        </div>
      </div>
      <div v-if="auditError" class="alert alert-error py-2"><span class="text-sm">{{ auditError }}</span></div>
      <div v-else-if="auditEvents.length === 0" class="text-center py-6 text-sm text-base-content/45 border border-dashed border-base-300 rounded-lg">
        No events yet.
      </div>
      <div v-else class="overflow-x-auto rounded-lg border border-base-300/60 max-h-96">
        <table class="table table-xs">
          <thead>
            <tr class="text-[10px] uppercase tracking-wider text-base-content/45">
              <th>When</th>
              <th>Actor</th>
              <th>Action</th>
              <th>Where</th>
              <th>Detail</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="e in auditEvents" :key="e.id">
              <td class="whitespace-nowrap">{{ formatWhen(e.timestamp) }}</td>
              <td class="truncate max-w-[20ch]">{{ e.actor || 'anonymous' }}</td>
              <td class="font-mono">{{ e.action }}</td>
              <td class="truncate max-w-[24ch]">
                {{ e.collection_id || '' }}<span v-if="e.document_id"> · {{ e.document_id.slice(0, 8) }}</span><span v-if="e.target"> · {{ e.target }}</span>
              </td>
              <td class="max-w-[40ch] truncate" :title="detailText(e.detail)">{{ detailText(e.detail) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="text-[11px] text-base-content/45 mt-1">
        Kept for {{ auditRetention ? auditRetention + ' days' : 'ever' }} (AUDIT_RETENTION_DAYS).
      </p>
    </section>

    <!-- ═══ Blocklist, acknowledgements, suspension ═══ -->
    <section aria-labelledby="admin-blocklist">
      <h2 id="admin-blocklist" class="text-sm font-semibold uppercase tracking-wider text-base-content/60 mb-3">Blocklist &amp; people</h2>
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div class="rounded-lg border border-base-300/60 bg-base-100 p-4 space-y-3">
          <div class="text-xs font-medium">Blocked file hashes</div>
          <p class="text-[11px] text-base-content/50">A removed-and-blocked file cannot be re-added to any collection. Add a sha256 by hand to block a file you do not hold.</p>
          <div class="flex flex-wrap gap-2">
            <input v-model="newHash" class="input input-bordered input-xs flex-1 min-w-[10rem] font-mono" placeholder="sha256 (64 hex)" aria-label="Hash to block" />
            <input v-model="newHashReason" class="input input-bordered input-xs w-32" placeholder="reason" aria-label="Reason" />
            <button class="btn btn-xs btn-outline" :disabled="!newHash.trim()" @click="blockHash">Block</button>
          </div>
          <div v-if="blocked.length === 0" class="text-xs text-base-content/45">Nothing blocked.</div>
          <ul v-else class="space-y-1 max-h-48 overflow-y-auto">
            <li v-for="h in blocked" :key="h.content_hash" class="flex items-center gap-2 text-xs">
              <code class="font-mono text-[11px] truncate flex-1" :title="h.content_hash">{{ h.content_hash.slice(0, 16) }}…</code>
              <span class="truncate max-w-[16ch] text-base-content/60" :title="h.filename || ''">{{ h.filename || '' }}</span>
              <span class="text-base-content/45 whitespace-nowrap">{{ formatDay(h.blocked_at) }}</span>
              <button class="btn btn-ghost btn-xs" @click="unblockHash(h.content_hash)">Unblock</button>
            </li>
          </ul>
        </div>

        <div class="space-y-4">
          <div class="rounded-lg border border-base-300/60 bg-base-100 p-4 space-y-2">
            <div class="text-xs font-medium">Suspend an identity</div>
            <p class="text-[11px] text-base-content/50">Revokes every MCP token they hold. Their sources stay until you remove them.</p>
            <div class="flex flex-wrap gap-2">
              <input v-model="suspendEmail" type="email" class="input input-bordered input-xs flex-1 min-w-[10rem]" placeholder="person@example.com" aria-label="Identity to suspend" />
              <input v-model="suspendReason" class="input input-bordered input-xs w-32" placeholder="reason" aria-label="Reason" />
              <button class="btn btn-xs btn-error btn-outline" :disabled="!suspendEmail.trim()" @click="suspend">Suspend</button>
            </div>
          </div>
          <div class="rounded-lg border border-base-300/60 bg-base-100 p-4 space-y-2">
            <div class="text-xs font-medium">Policy acknowledgements <span class="text-base-content/45 font-normal">(version {{ aupAcks.version }})</span></div>
            <div v-if="aupAcks.acknowledgements.length === 0" class="text-xs text-base-content/45">
              {{ aupAcks.required ? 'Nobody has accepted the current version yet.' : 'Acknowledgement is not required on this deployment.' }}
            </div>
            <ul v-else class="space-y-0.5 max-h-40 overflow-y-auto text-xs">
              <li v-for="a in aupAcks.acknowledgements" :key="a.user_id + a.version" class="flex justify-between gap-2">
                <span class="truncate">{{ a.user_id }}</span>
                <span class="text-base-content/45 whitespace-nowrap">v{{ a.version }} · {{ formatDay(a.accepted_at) }}</span>
              </li>
            </ul>
          </div>
        </div>
      </div>
    </section>

  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { RefreshCw, ShieldCheck } from 'lucide-vue-next'
import http from '../utils/http'
import { useUserStore } from '../stores/userStore'
import { useUiStore } from '../stores/uiStore'
import { useReviewStore } from '../stores/reviewStore'
import {
  policyBadge, policyFlagPages, categoryLabel, priorityBadge, injectionEvidence,
  KIND_LABELS, REVIEW_FILTERS, filterReviewItems,
} from '../utils/governance'

const userStore = useUserStore()
const ui = useUiStore()
const reviewStore = useReviewStore()

// ── Content review ─────────────────────────────────────────────────────
const review = ref({ items: [], quarantined: [], flagged: [], reports: [] })
const reviewError = ref('')
const reviewLoading = ref(true)
const expanded = ref('')
const busy = ref('')
const reviewFilter = ref('all')
const reviewQueue = computed(() => review.value.items || [])
const visibleQueue = computed(() => filterReviewItems(reviewQueue.value, reviewFilter.value))
const filterCount = (id) => filterReviewItems(reviewQueue.value, id).length
const key = (d) => `${d.collection_id}:${d.document_id}`
const toggleDetails = (d) => { expanded.value = expanded.value === key(d) ? '' : key(d) }

// ── Selection for bulk decisions ───────────────────────────────────────
// Keyed by collection:document so a selection survives a reload of the queue;
// rows that left the queue simply stop counting.
const selected = ref(new Set())
const selectedShown = computed(() => visibleQueue.value.filter((d) => selected.value.has(key(d))))
const allShownSelected = computed(() => visibleQueue.value.length > 0 && selectedShown.value.length === visibleQueue.value.length)
const toggleSelected = (d) => {
  const next = new Set(selected.value)
  if (next.has(key(d))) next.delete(key(d))
  else next.add(key(d))
  selected.value = next
}
const toggleSelectAllShown = () => {
  const next = new Set(selected.value)
  if (allShownSelected.value) visibleQueue.value.forEach((d) => next.delete(key(d)))
  else visibleQueue.value.forEach((d) => next.add(key(d)))
  selected.value = next
}
const clearSelection = () => { selected.value = new Set() }

// What the green button does depends on why the document is here.
const approveLabel = (d) => {
  if (d.policy_status === 'quarantined') return 'Release'
  if (d.kinds.includes('policy')) return 'Approve'
  return 'Looks fine'
}
const approveHint = (d) => {
  const does = []
  if (d.kinds.includes('policy')) does.push(d.policy_status === 'quarantined' ? 'lifts the hold' : 'clears the flag')
  if (d.kinds.includes('injection')) does.push('dismisses the injection warnings')
  if (d.kinds.includes('report')) does.push('closes the reports')
  return `Marks it reviewed: ${does.join(', ')}. The findings stay on record.`
}

const loadReview = async () => {
  reviewError.value = ''
  try {
    const resp = await http.get('/api/admin/review')
    review.value = resp.data
    reviewStore.setSummary(resp.data.summary)
    if (reviewFilter.value !== 'all' && filterCount(reviewFilter.value) === 0) reviewFilter.value = 'all'
  } catch (err) {
    reviewError.value = err.message || 'Could not load the review queue'
  } finally {
    reviewLoading.value = false
  }
}

const approve = async (d) => {
  busy.value = key(d)
  try {
    await http.post(`/api/admin/documents/${d.collection_id}/${d.document_id}/approve`, { note: '' })
    ui.notify(`Marked ${d.filename || 'the document'} as reviewed.`, 'success')
    await Promise.all([loadReview(), loadAudit()])
  } catch (err) {
    ui.toastError(err, 'Could not approve the document')
  } finally {
    busy.value = ''
  }
}

// One decision for many rows: a re-index or a tuning change can queue dozens
// of documents at once, and clicking them away one by one is a chore.
const bulkBusy = ref('')
const refs = (rows) => rows.map((d) => ({ collection_id: d.collection_id, document_id: d.document_id }))

const bulkApprove = async (rows, label) => {
  const items = refs(rows)
  if (!items.length) return
  if (!window.confirm(`Mark ${items.length === 1 ? 'this document' : `these ${items.length} documents`} as reviewed and fine? Held documents become visible again.`)) return
  bulkBusy.value = label
  try {
    const resp = await http.post('/api/admin/review/resolve', { items, note: '' })
    const n = resp.data?.resolved ?? items.length
    const skipped = resp.data?.skipped?.length || 0
    ui.notify(`Marked ${n} document${n === 1 ? '' : 's'} as reviewed.` + (skipped ? ` ${skipped} had already gone.` : ''), 'success')
    clearSelection()
    await Promise.all([loadReview(), loadAudit()])
  } catch (err) {
    ui.toastError(err, 'Could not approve the documents')
  } finally {
    bulkBusy.value = ''
  }
}
const approveAll = () => bulkApprove(visibleQueue.value, 'approve-all')
const approveSelected = () => bulkApprove(selectedShown.value, 'approve')

const removeSelected = async (block) => {
  const items = refs(selectedShown.value)
  if (!items.length) return
  const what = items.length === 1 ? `"${selectedShown.value[0].filename}"` : `these ${items.length} documents`
  if (!window.confirm(block
    ? `Remove ${what} and block the file${items.length === 1 ? '' : 's'} from being re-added?`
    : `Remove ${what}?`)) return
  bulkBusy.value = block ? 'remove-block' : 'remove'
  try {
    const resp = await http.post('/api/admin/review/remove', { items, block, reason: '' })
    const n = resp.data?.removed ?? items.length
    const skipped = resp.data?.skipped?.length || 0
    ui.notify(
      (block ? `Removed and blocked ${n} document${n === 1 ? '' : 's'}.` : `Removed ${n} document${n === 1 ? '' : 's'}.`) +
      (skipped ? ` ${skipped} had already gone.` : ''),
      'success',
    )
    clearSelection()
    await Promise.all([loadReview(), loadAudit(), loadBlocked()])
  } catch (err) {
    ui.toastError(err, 'Could not remove the documents')
  } finally {
    bulkBusy.value = ''
  }
}

// ── Scanner tuning ─────────────────────────────────────────────────────
const tuning = ref({ muted: [], allow_phrases: [], noisy: [], patterns: [], noisy_rule: null })
const tuningLoading = ref(false)
const tuningLoaded = ref(false)
const tuningError = ref('')
const tuningBusy = ref(false)
const newAllowPhrase = ref('')

const loadTuning = async () => {
  tuningLoading.value = !tuningLoaded.value
  tuningError.value = ''
  try {
    tuning.value = (await http.get('/api/admin/injection-tuning')).data
    tuningLoaded.value = true
  } catch (err) {
    tuningError.value = err.message || 'Could not load the scanner tuning'
  } finally {
    tuningLoading.value = false
  }
}
const onTuningToggle = (event) => { if (event.target.open && !tuningLoaded.value) loadTuning() }

// One call for every lever; the server answers with the whole tuning, and the
// queue is reloaded because muting a rule can empty it.
const tune = async (action, body = {}) => {
  tuningBusy.value = true
  try {
    const resp = await http.post('/api/admin/injection-tuning', { action, ...body })
    tuning.value = resp.data
    tuningLoaded.value = true
    const said = {
      mute: `Muted ${body.pattern}. Warnings from it are no longer counted anywhere.`,
      unmute: `Unmuted ${body.pattern}.`,
      allow: 'Allowed that wording everywhere.',
      disallow: 'Removed the allowed wording.',
      reset_feedback: body.pattern ? `Reset the statistics for ${body.pattern}.` : 'Reset all rule statistics.',
    }
    ui.notify(said[action] || 'Tuning updated.', 'success')
    await Promise.all([loadReview(), loadAudit()])
  } catch (err) {
    ui.toastError(err, 'Could not change the scanner tuning')
  } finally {
    tuningBusy.value = false
  }
}
const addAllowPhrase = async () => {
  const phrase = newAllowPhrase.value.trim()
  if (phrase.length < 4) return
  await tune('allow', { phrase })
  newAllowPhrase.value = ''
}

const remove = async (d, block) => {
  const what = d.filename || d.document_id
  if (!window.confirm(block ? `Remove "${what}" and block the file from being re-added?` : `Remove "${what}"?`)) return
  busy.value = key(d)
  try {
    await http.post(`/api/admin/documents/${d.collection_id}/${d.document_id}/remove`, { block, reason: '' })
    ui.notify(block ? `Removed and blocked ${what}.` : `Removed ${what}.`, 'success')
    await Promise.all([loadReview(), loadAudit(), loadBlocked()])
  } catch (err) {
    ui.toastError(err, 'Could not remove the document')
  } finally {
    busy.value = ''
  }
}

// ── Policy settings (persisted through /api/config) ────────────────────
const policyForm = reactive({
  content_policy_action: 'flag',
  injection_scan_action: 'review',
  content_policy_llm_review: false,
  content_policy_llm_provider: '',
  content_policy_llm_model: '',
  pii_scan_action: 'label',
  pii_flag_threshold: 10,
  pii_entities: '',
  pii_redact_outputs: false,
  aup_required: false,
  aup_version: '1',
  aup_text: '',
})
const policySaving = ref(false)
const policySaved = ref(false)

const loadPolicy = async () => {
  try {
    const resp = await http.get('/api/config')
    for (const k of Object.keys(policyForm)) {
      if (resp.data[k] !== undefined && resp.data[k] !== null) policyForm[k] = resp.data[k]
    }
    policyForm.aup_version = String(policyForm.aup_version)
  } catch { /* leave defaults */ }
}

const savePolicy = async () => {
  policySaving.value = true
  policySaved.value = false
  try {
    const resp = await http.post('/api/config', { ...policyForm })
    if (resp.data?.success === false) {
      ui.notify((resp.data.errors || []).join('; ') || 'Could not save', 'error')
    } else {
      policySaved.value = true
      setTimeout(() => { policySaved.value = false }, 2500)
      await Promise.all([loadReview(), userStore.loadCurrentUser(), loadAcks()])
    }
  } catch (err) {
    ui.toastError(err, 'Could not save policy settings')
  } finally {
    policySaving.value = false
  }
}

// ── Audit trail ────────────────────────────────────────────────────────
const auditEvents = ref([])
const auditActions = ref([])
const auditRetention = ref(0)
const auditError = ref('')
const auditFilter = reactive({ action: '', actor: '' })
const auditCsvHref = computed(() => {
  const p = new URLSearchParams({ format: 'csv', limit: '2000' })
  if (auditFilter.action) p.set('action', auditFilter.action)
  if (auditFilter.actor) p.set('actor', auditFilter.actor)
  return `/api/admin/audit?${p.toString()}`
})

const loadAudit = async () => {
  auditError.value = ''
  try {
    const params = { limit: 200 }
    if (auditFilter.action) params.action = auditFilter.action
    if (auditFilter.actor.trim()) params.actor = auditFilter.actor.trim()
    const resp = await http.get('/api/admin/audit', { params })
    auditEvents.value = resp.data.events || []
    auditActions.value = resp.data.actions || []
    auditRetention.value = resp.data.retention_days || 0
  } catch (err) {
    auditError.value = err.message || 'Could not load the audit trail'
  }
}

const detailText = (detail) => {
  if (!detail) return ''
  if (typeof detail === 'string') return detail
  return Object.entries(detail)
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : typeof v === 'object' ? JSON.stringify(v) : v}`)
    .join(' · ')
}

// ── Blocklist / suspension / acknowledgements ──────────────────────────
const blocked = ref([])
const newHash = ref('')
const newHashReason = ref('')
const suspendEmail = ref('')
const suspendReason = ref('')
const aupAcks = ref({ required: false, version: '1', acknowledgements: [] })

const loadBlocked = async () => {
  try {
    blocked.value = (await http.get('/api/admin/blocked-hashes')).data.hashes || []
  } catch { blocked.value = [] }
}

const blockHash = async () => {
  try {
    await http.post('/api/admin/blocked-hashes', { content_hash: newHash.value.trim(), reason: newHashReason.value })
    newHash.value = ''
    newHashReason.value = ''
    await Promise.all([loadBlocked(), loadAudit(), loadReview()])
  } catch (err) {
    ui.toastError(err, 'Could not block that hash')
  }
}

const unblockHash = async (h) => {
  try {
    await http.delete(`/api/admin/blocked-hashes/${h}`)
    await Promise.all([loadBlocked(), loadAudit(), loadReview()])
  } catch (err) {
    ui.toastError(err, 'Could not unblock that hash')
  }
}

const suspend = async () => {
  const email = suspendEmail.value.trim()
  if (!window.confirm(`Suspend ${email}? Their MCP tokens are revoked immediately.`)) return
  try {
    const resp = await http.post(`/api/admin/users/${encodeURIComponent(email)}/suspend`, { reason: suspendReason.value })
    const r = resp.data
    ui.notify(
      `Suspended ${email}: ${r.tokens_revoked} token${r.tokens_revoked === 1 ? '' : 's'} revoked` +
      '.',
      'success',
    )
    suspendEmail.value = ''
    suspendReason.value = ''
    await loadAudit()
  } catch (err) {
    ui.toastError(err, 'Could not suspend that identity')
  }
}

const loadAcks = async () => {
  try {
    aupAcks.value = (await http.get('/api/admin/aup-acknowledgements')).data
  } catch { /* leave */ }
}

const formatWhen = (iso) => {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleString() } catch { return iso }
}

const loading = ref(false)
const sys = ref({})
const statsError = ref('')
const usageDays = ref(30)
const byUser = ref([])
const byDay = ref([])
const usageError = ref('')

const rateLimitRejected = computed(() => {
  const classes = sys.value.rate_limit?.classes || {}
  return formatNum(Object.values(classes).reduce((n, c) => n + (c.rejected || 0), 0))
})

const totalTokens = (row) => (row?.input_tokens || 0) + (row?.output_tokens || 0)

const maxDayTokens = computed(() =>
  Math.max(1, ...byDay.value.map((d) => totalTokens(d)))
)
const dayBarHeight = (day) =>
  `${Math.max(4, Math.round((totalTokens(day) / maxDayTokens.value) * 100))}%`

const cacheHitRate = (row) =>
  row.turns ? `${Math.round(((row.cache_hits || 0) / row.turns) * 100)}%` : '0%'

const formatNum = (n) => (n ?? 0).toLocaleString()
const formatDay = (iso) => (iso ? iso.slice(0, 10) : '—')
const formatUptime = (s) => {
  if (s == null) return '—'
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`
}

const loadStats = async () => {
  statsError.value = ''
  try {
    const resp = await http.get('/api/admin/stats')
    sys.value = resp.data
  } catch (err) {
    statsError.value = err.message || 'Could not load system stats'
  }
}

const loadUsage = async () => {
  usageError.value = ''
  try {
    const resp = await http.get(`/api/admin/usage?days=${usageDays.value}`)
    byUser.value = resp.data.by_user || []
    byDay.value = resp.data.by_day || []
  } catch (err) {
    usageError.value = err.message || 'Could not load usage data'
  }
}

const loadAll = async () => {
  loading.value = true
  try {
    await Promise.all([
      loadStats(), loadUsage(), loadReview(), loadPolicy(), loadAudit(), loadBlocked(), loadAcks(),
    ])
  } finally {
    loading.value = false
  }
}

onMounted(loadAll)
</script>
