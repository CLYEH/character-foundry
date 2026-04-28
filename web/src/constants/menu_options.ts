/**
 * Phase 1 hardcoded template menu options. Each option is `{ value, label_zh }`;
 * backend reconciler is responsible for translating the value to English prompt
 * fragments. Values intentionally live in this single file so a future move to
 * `/v1/meta` (see STATUS.md M5) can swap the import without touching the panel.
 */

export type MenuKey =
  | 'gender'
  | 'eye_shape'
  | 'nose_shape'
  | 'hair_style'
  | 'skin_tone'
  | 'body_type'
  | 'art_style'

export interface MenuOption {
  value: string
  label_zh: string
}

export interface MenuField {
  key: MenuKey
  label_zh: string
  options: MenuOption[]
}

export const MENU_FIELDS: MenuField[] = [
  {
    key: 'gender',
    label_zh: '性別',
    options: [
      { value: 'female', label_zh: '女性' },
      { value: 'male', label_zh: '男性' },
      { value: 'androgynous', label_zh: '中性' },
    ],
  },
  {
    key: 'eye_shape',
    label_zh: '眼型',
    options: [
      { value: 'round', label_zh: '圓眼' },
      { value: 'almond', label_zh: '杏眼' },
      { value: 'narrow', label_zh: '細長眼' },
      { value: 'large', label_zh: '大眼' },
    ],
  },
  {
    key: 'nose_shape',
    label_zh: '鼻型',
    options: [
      { value: 'straight', label_zh: '直挺' },
      { value: 'small', label_zh: '小巧' },
      { value: 'aquiline', label_zh: '鷹勾' },
      { value: 'flat', label_zh: '扁平' },
    ],
  },
  {
    key: 'hair_style',
    label_zh: '髮型',
    options: [
      { value: 'long_straight', label_zh: '長直髮' },
      { value: 'long_wavy', label_zh: '長捲髮' },
      { value: 'short', label_zh: '短髮' },
      { value: 'ponytail', label_zh: '馬尾' },
      { value: 'bun', label_zh: '盤髮' },
      { value: 'bob', label_zh: '鮑伯' },
    ],
  },
  {
    key: 'skin_tone',
    label_zh: '膚色',
    options: [
      { value: 'fair', label_zh: '白皙' },
      { value: 'medium', label_zh: '小麥' },
      { value: 'tan', label_zh: '古銅' },
      { value: 'deep', label_zh: '深膚' },
    ],
  },
  {
    key: 'body_type',
    label_zh: '體型',
    options: [
      { value: 'slim', label_zh: '纖細' },
      { value: 'average', label_zh: '一般' },
      { value: 'athletic', label_zh: '健壯' },
      { value: 'plus', label_zh: '豐腴' },
    ],
  },
  {
    key: 'art_style',
    label_zh: '風格',
    options: [
      { value: 'ink_wash', label_zh: '水墨畫' },
      { value: 'anime', label_zh: '日系動漫' },
      { value: 'realistic', label_zh: '寫實' },
      { value: 'cartoon', label_zh: '卡通' },
      { value: 'watercolor', label_zh: '水彩' },
    ],
  },
]

export type MenuSelections = Partial<Record<MenuKey, string>>

export const FREEFORM_MAX_LENGTH = 500
