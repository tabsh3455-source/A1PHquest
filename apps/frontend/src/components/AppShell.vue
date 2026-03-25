<template>
  <div class="aq-page">
    <div class="aq-shell">
      <header class="aq-topbar">
        <router-link class="aq-brand" to="/dashboard">
          <span class="aq-brand-mark">A1</span>
          <span class="aq-brand-copy">
            <span class="aq-brand-name">A1phquest</span>
            <span class="aq-brand-subtitle">Quant strategy console</span>
          </span>
        </router-link>
        <div class="aq-toolbar">
          <slot name="toolbar" />
        </div>
      </header>
      <main class="aq-main">
        <slot />
      </main>
    </div>
  </div>
</template>
