<template>
  <div v-if="!auth.ready" class="min-h-screen grid place-items-center bg-base-200" aria-busy="true">
    <span class="loading loading-spinner loading-md" role="status" aria-label="Loading"></span>
  </div>
  <SignInPage v-else-if="auth.needsSignIn" />
  <slot v-else />
</template>

<script setup>
import { onMounted } from 'vue'
import SignInPage from './SignInPage.vue'
import { useAuthStore } from '../stores/authStore'

const auth = useAuthStore()
onMounted(() => auth.init())
</script>
