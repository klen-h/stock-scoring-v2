# Skill：个股展示规范（名称带代码 · 名称跳详情 · 代码跳雪球）

> 目标：把"股票名称出现必须带代码、名称跳本地详情、代码跳雪球"这条口径
> 变成**唯一正确写法**，避免同一条规则在不同页面反复遗漏（已发生过多次：
> 两套 helper 并存、港股指数硬编码、部分回复只写名称不写代码）。

## 触发条件

- 新增或修改**任何展示个股的 Vue 页面/组件/卡片**（榜单、持仓、自选、观察池、日志、推送卡…）。
- 在回复、日报、推演、文档里**提到具体个股**。
- 看到硬编码 `xueqiu.com`、`#/stock/`、自己拼 SH/SZ 前缀的代码。

## 前置检查（动手前，30 秒）

1. `grep -rn "xueqiu\.com" frontend/src` —— 若结果含**个股**代码，先改成 helper。
   白名单：港股指数 `HKHSTECH` 这类（走 `xqIndexUrl('hk', ...)`，见下）。
2. 确认要展示的是**个股**还是**指数**：
   - 个股 → `xqUrl(code)`（自动判 SH/SZ/BJ）
   - 指数 → `xqIndexUrl(prefix, code)`（**必须显式给 prefix**）
   - ⚠️ `000001` 撞码：上证指数 SH000001 vs 平安银行 SZ000001，绝不能混。
3. 确认该项目**已有** helper，不要新写：
   `frontend/src/composables/displayMeta.js` 的 `stockHref` / `xqUrl` / `xqIndexUrl`
   与 `frontend/src/components/StockName.vue`。

## 步骤

### A. 展示个股（推荐：共享组件，不可能漏代码）

```vue
<script setup>
import StockName from '../components/StockName.vue'
</script>

<template>
  <td><StockName :code="row.code" :name="row.name" /></td>
</template>
```

组件已实现三件事：名称带代码、名称 → `#/stock/{code}`、代码 → 雪球。
**用它就不需要再手写任何链接。**

### B. 只要一个链接时（不展示完整名称+代码）

```vue
<script setup>
import { stockHref, xqUrl } from '../composables/displayMeta'
</script>

<template>
  <a :href="stockHref(code)" target="_blank" rel="noopener">{{ name }}</a>
  <a :href="xqUrl(code)" target="_blank" rel="noopener" class="font-mono">{{ code }}</a>
</template>
```

### C. 指数

```js
xqIndexUrl('sh', '000001')    // 上证指数
xqIndexUrl('hk', 'HSTECH')    // 恒生科技
```
prefix 不在 `sh/sz/bj/csi/hk` 白名单时返回 `''` ⇒ 调用方退回纯文本。

### D. 文本 / 回复 / 日报

```
工商银行（601398）收 9.23，高新发展（000628）收 44.66。
```
每个个股都带代码，不得只给第一个带、后面省略。

## 红线（违反即回滚）

- ❌ 硬编码 `https://xueqiu.com/...`（唯一例外：指数且 helper 不支持时，须在门禁白名单登记）
- ❌ 硬编码 `#/stock/...`、`/stock/...` 当链接
- ❌ 自己写 `code.startsWith('6') ? 'SH' : 'SZ'` 这类前缀判断
- ❌ 新代码 import `composables/stockUtils.js` 的 `getXueqiuUrl`（兼容 re-export，但新代码一律走 displayMeta）
- ❌ 指数走 `xqUrl()`（会指错标的）
- ❌ 只写股票名称不带代码，包括小名（"工行"也要写成"工商银行（601398）"）

## 验证方式

```bash
# 1. 硬编码扫描（必须 0 违规；白名单例外会列出）
python scripts/check_stock_display.py

# 2. 前端构建
cd frontend && npm run build:server

# 3. 目视确认：每个展示个股的卡片，名称可点进详情、代码可点进雪球
```

## 已踩过的坑（别重复）

| 现象 | 原因 |
|---|---|
| 两套雪球 helper 并存 | `stockUtils.getXueqiuUrl()` 不支持 BJ，与 `displayMeta.xqUrl()` 行为不同 |
| 恒生科技硬编码 URL | `xqIndexUrl()` 早期只支持 `sh/sz/bj/csi`，漏了 `hk` |
| 回复里只写名称 | 规则只在代码注释里，没进 always 规则；本 skill + RULE 已补齐 |
