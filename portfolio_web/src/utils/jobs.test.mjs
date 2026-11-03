/**
 * 后台管理页展示纯函数的单测（node --test）。
 *
 * 锁定的口径：
 *   - 时间按**字符串拆字段**，不做时区换算 —— 后端给的是 Asia/Shanghai 墙上时间，
 *     浏览器在任何时区都必须显示「配置里写的 15:05」；
 *   - 非法/空值一律给「—」，绝不渲染 undefined / Invalid Date；
 *   - 状态未知时回落 muted + 原文，不吞掉后端新加的状态。
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  formatDateTime,
  formatDuration,
  formatMinute,
  formatTimeOnly,
  nextRunText,
  overviewOf,
  statusMeta,
  summarizeRun,
  triggerLabel,
} from './jobs.js'

test('formatDateTime：拆字段格式化，不受浏览器时区影响', () => {
  assert.equal(formatDateTime('2026-11-03T15:05:12'), '11-03 15:05:12')
  assert.equal(formatDateTime('2026-01-02 09:00'), '01-02 09:00:00')
  assert.equal(formatDateTime('2026-11-03T15:05:12.123456'), '11-03 15:05:12')
})

test('formatDateTime：空值与脏值都给「—」', () => {
  assert.equal(formatDateTime(null), '—')
  assert.equal(formatDateTime(''), '—')
  assert.equal(formatDateTime(undefined), '—')
  assert.equal(formatDateTime('not-a-date'), '—')
})

test('formatMinute / formatTimeOnly', () => {
  assert.equal(formatMinute('2026-11-03T15:05:12'), '11-03 15:05')
  assert.equal(formatTimeOnly('2026-11-03T15:05:12'), '15:05')
  assert.equal(formatTimeOnly(null), '—')
})

test('formatDuration：ms / s / m 三档', () => {
  assert.equal(formatDuration(0), '0ms')
  assert.equal(formatDuration(820), '820ms')
  assert.equal(formatDuration(1200), '1.2s')
  assert.equal(formatDuration(12_000), '12s')
  assert.equal(formatDuration(65_000), '1m 05s')
  assert.equal(formatDuration(null), '—')
  assert.equal(formatDuration(-1), '—')
})

test('nextRunText：今天 / 明天 / 具体日期', () => {
  const now = new Date(2026, 10, 3, 9, 0, 0)   // 2026-11-03 09:00 本地

  assert.equal(nextRunText('2026-11-03T15:05:00', now), '今天 15:05')
  assert.equal(nextRunText('2026-11-04T15:30:00', now), '明天 15:30')
  assert.equal(nextRunText('2026-12-01T15:30:00', now), '12-01 15:30')
  assert.equal(nextRunText(null, now), '—')
})

test('nextRunText：跨月边界，「明天」要算对', () => {
  const now = new Date(2026, 10, 30, 23, 0, 0)  // 11-30
  assert.equal(nextRunText('2026-12-01T15:05:00', now), '明天 15:05')
})

test('statusMeta：五个状态全覆盖，未知回落 muted', () => {
  assert.deepEqual(statusMeta('success'), { label: '成功', tone: 'success' })
  assert.deepEqual(statusMeta('partial'), { label: '部分失败', tone: 'warning' })
  assert.deepEqual(statusMeta('failed'), { label: '失败', tone: 'danger' })
  assert.deepEqual(statusMeta('skipped'), { label: '跳过', tone: 'muted' })
  assert.deepEqual(statusMeta('running'), { label: '运行中', tone: 'info' })
  assert.deepEqual(statusMeta('weird'), { label: 'weird', tone: 'muted' })
  assert.equal(statusMeta(null).label, '未知')
})

test('summarizeRun：成功数/总数，跳过给原因', () => {
  assert.equal(
    summarizeRun({ status: 'success', succeeded: 5, failed: 0, total: 5 }),
    '5/5'
  )
  assert.equal(
    summarizeRun({ status: 'partial', succeeded: 4, failed: 1, total: 5 }),
    '4/5（失败 1）'
  )
  assert.equal(
    summarizeRun({ status: 'skipped', message: '11-03 的快照已齐，无需更新' }),
    '11-03 的快照已齐，无需更新'
  )
  assert.equal(summarizeRun(null), '—')
})

test('triggerLabel：manual 才是手动', () => {
  assert.equal(triggerLabel('manual'), '手动')
  assert.equal(triggerLabel('schedule'), '定时')
  assert.equal(triggerLabel(undefined), '定时')
})

test('overviewOf：取最近一次运行、统计启用数与失败数', () => {
  const jobs = [
    {
      name: '投资组合价格刷新',
      enabled: true,
      last_run: { started_at: '2026-11-03T15:05:00', status: 'success' },
      status_counts: { success: 3, failed: 1 },
    },
    {
      name: '观察池每日快照',
      enabled: false,
      last_run: { started_at: '2026-11-03T15:30:00', status: 'failed' },
      status_counts: { failed: 2 },
    },
  ]
  const ov = overviewOf(jobs)
  assert.equal(ov.total, 2)
  assert.equal(ov.enabled, 1)
  assert.equal(ov.failures, 3)
  assert.equal(ov.latest.started_at, '2026-11-03T15:30:00')
  assert.equal(ov.latest.jobName, '观察池每日快照')
  assert.equal(ov.latest.status, 'failed')
})

test('overviewOf：空列表 / 全都没跑过也不炸', () => {
  assert.deepEqual(overviewOf([]), { total: 0, enabled: 0, latest: null, failures: 0 })
  const ov = overviewOf([{ name: 'x', enabled: false, last_run: null }])
  assert.equal(ov.latest, null)
  assert.equal(ov.failures, 0)
  assert.equal(overviewOf(null).total, 0)
})
