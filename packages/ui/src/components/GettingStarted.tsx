import React from 'react';
import { Icon } from './Icon';

export function GettingStarted({openProject, openConnections, openDiagnostics, connected}: {
  openProject(): void; openConnections(): void; openDiagnostics(): void; connected: boolean;
}) {
  return <div className="getting-started" data-testid="getting-started">
    <div className="page-intro"><span className="intro-icon"><Icon name="book" size={24}/></span>
      <h2>第一次使用</h2></div>
    <section className="guide-step"><span className="step-number">1</span><div><h3>打开项目</h3>
      <p>选择代码文件夹。首次为只读，修改或运行代码前需授权。</p>
      <button className="secondary" disabled={!connected} onClick={openProject}>选择项目 <Icon name="folder"/></button></div></section>
    <section className="guide-step"><span className="step-number">2</span><div><h3>连接模型</h3>
      <p>填写服务商、地址、模型和密钥，按系统提示保存。桌面端需单独配置。</p>
      <button className="secondary" data-testid="guide-connections" onClick={openConnections}>模型连接 <Icon name="arrow" size={16}/></button></div></section>
    <section className="guide-step"><span className="step-number">3</span><div><h3>开始任务</h3>
      <p>在工作区选择模型并新建会话。Enter 发送，Shift + Enter 换行。</p></div></section>
    <section className="guide-step"><span className="step-number"><Icon name="shield"/></span><div><h3>发送按钮不可用时</h3>
      <p>检查模型连接、项目授权和执行环境。严格沙盒未就绪时不能运行任务；桌面端可在环境诊断中明确选择本机执行，命令以当前用户权限运行，无 OS 沙盒隔离。切换并重启后请新建会话。</p>
      <button className="secondary" onClick={openDiagnostics}>检查执行环境 <Icon name="arrow"/></button></div></section>
    <details className="guide-launch"><summary>启动方式</summary>
      <p>Windows 安装版：从开始菜单打开 ForgeCode。</p>
      <p>源码开发：双击仓库根目录的 <code>Start-ForgeCode.cmd</code>，或运行：</p><pre>npm run dev:desktop</pre>
      <p>完整说明：<code>docs/install/desktop-quickstart.md</code></p></details>
  </div>;
}
