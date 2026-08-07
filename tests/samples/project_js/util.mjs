// 项目级测试样本：JS 模块 A，定义跨文件 import 的函数/类/常量。

export const BASE = 10;
export function add(a, b) {
  const total = a + b;
  return total;
}
export class Calc {
  constructor(base) {
    this.base = base;
  }
  mul(x) {
    return x * this.base;
  }
}
