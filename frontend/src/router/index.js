import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'
import TaskView from '../views/TaskView.vue'

// 首页输入仓库，结果页按 task_id 展示；任务表在后端内存里，路由参数就是唯一凭证
export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    { path: '/tasks/:id', name: 'task', component: TaskView, props: true },
  ],
})
