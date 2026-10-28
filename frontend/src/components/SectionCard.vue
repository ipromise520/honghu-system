<template>
  <section class="hh-card hh-reveal" :class="{ 'hh-card--plain': !title && !$slots.actions }">
    <header v-if="title || $slots.actions" class="hh-card__head">
      <div class="hh-card__titles">
        <span v-if="icon" class="hh-card__icon"><el-icon :size="16"><component :is="icon" /></el-icon></span>
        <span class="hh-card__title">{{ title }}</span>
        <span v-if="sub" class="hh-card__sub">{{ sub }}</span>
      </div>
      <div class="hh-card__actions"><slot name="actions" /></div>
    </header>
    <div class="hh-card__body" :class="bodyClass"><slot /></div>
  </section>
</template>

<script setup>
defineProps({
  title: { type: String, default: '' },
  sub: { type: String, default: '' },
  icon: { type: [String, Object], default: '' },
  bodyClass: { type: String, default: '' },
})
</script>

<style scoped>
.hh-card {
  background: var(--hh-surface);
  border: 1px solid var(--hh-border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-1);
  margin-bottom: var(--space-4);
  transition: box-shadow var(--dur-base) var(--ease-out);
}
.hh-card:hover { box-shadow: var(--shadow-2); }
.hh-card--plain { box-shadow: var(--shadow-1); }
.hh-card__head {
  display: flex; align-items: center; justify-content: space-between;
  gap: var(--space-3);
  padding: 14px 16px;
  border-bottom: 1px solid var(--hh-border);
}
.hh-card__titles { display: flex; align-items: baseline; gap: 10px; min-width: 0; }
.hh-card__icon {
  display: inline-flex; align-items: center; justify-content: center;
  width: 26px; height: 26px; border-radius: var(--radius-sm);
  background: var(--hh-primary-weak); color: var(--hh-primary);
}
.hh-card__title { font-weight: 600; font-size: var(--fs-h3); color: var(--hh-text); }
.hh-card__sub { font-size: var(--fs-sm); color: var(--hh-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.hh-card__actions { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }
.hh-card__body { padding: 16px; }
@media (max-width: 768px) {
  .hh-card__head { padding: 12px; }
  .hh-card__body { padding: 12px; }
  .hh-card__sub { display: none; }
}
</style>
