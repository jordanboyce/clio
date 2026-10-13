<template>
  <AuthGate>
    <App />
  </AuthGate>
</template>

<script setup>
import App from './App.vue'
import AuthGate from './components/AuthGate.vue'
</script>
