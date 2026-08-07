// 项目级测试样本：JS 入口，跨文件 import util 模块。

import { BASE, add, Calc } from "./util.mjs";

const c = new Calc(2);
const msg = "result";
console.log(msg, add(1, 2));
console.log(BASE + c.mul(5));
