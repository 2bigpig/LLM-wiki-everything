import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import LibraryView from './features/library/LibraryView.vue'
import RecordView from './features/library/RecordView.vue'
import WikiView from './features/wiki/WikiView.vue'
import WikiDetailView from './features/wiki/WikiDetailView.vue'
import TasksView from './features/processing/TasksView.vue'
import SettingsView from './features/settings/SettingsView.vue'
import SearchView from './features/search/SearchView.vue'
import './style.css'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: LibraryView },
    { path: '/records/:id', component: RecordView },
    { path: '/wiki', component: WikiView },
    { path: '/wiki/:id', component: WikiDetailView },
    { path: '/tasks', component: TasksView },
    { path: '/settings', component: SettingsView },
    { path: '/search', component: SearchView },
    { path: '/graph', component: () => import('./features/graph/GraphView.vue') },
    { path: '/reviews', component: () => import('./features/review/ReviewsView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  scrollBehavior: (to) => to.hash ? { el: to.hash, top: 100 } : { top: 0 },
})
createApp(App).use(router).mount('#app')
