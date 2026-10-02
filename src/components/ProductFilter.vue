<script setup>
import { computed } from "vue";
import { useAppStore } from "../stores/app.js";

const store = useAppStore();

const products = computed(() => store.products);

function toggle(p) {
  store.productFilter = store.productFilter === p ? null : p;
}
</script>

<template>
  <div class="filter-bar">
    <span class="label">品种：</span>
    <a-checkable-tag :checked="store.productFilter == null" @change="store.productFilter = null">全部</a-checkable-tag>
    <a-checkable-tag
      v-for="p in products"
      :key="p"
      :checked="store.productFilter === p"
      @change="toggle(p)"
    >{{ p }}</a-checkable-tag>
  </div>
</template>

<style scoped>
.filter-bar { padding: 6px 8px 2px; }
.label { color: #666; font-size: 13px; margin-right: 4px; }
</style>
