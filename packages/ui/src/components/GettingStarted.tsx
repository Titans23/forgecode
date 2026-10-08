import React from 'react';
import { Icon } from './Icon';

export function GettingStarted({openProject, openConnections, openDiagnostics, connected}: {
  openProject(): void; openConnections(): void; openDiagnostics(): void; connected: boolean;
}) {
  return <div className="getting-started" data-testid="getting-started">
    <div className="page-intro"><span className="intro-icon"><Icon name="help" size={24}/></span>
      <h2>几步，开始你的第一个任务</h2><p>打开项目，连接模型，再告诉 ForgeCode 你想完成什么。</p></div>
    <section className="guide-step"><span className="step-number">1</span><div><h3>打开一个项目</h3>
      <p>选择存放代码的文件夹。首次打开时可以浏览文件；需要修改或运行代码时，再确认项目执行授权。</p>
      <button className="secondary" disabled={!connected} onClick={openProject}>选择项目 <Icon name="folder"/></button></div></section>
    <section className="guide-step"><span className="step-number">2</span><div><h3>连接你的模型</h3>
      <p>在连接设置中填写服务商、服务地址、模型名称和 API 密钥，按系统提示确认保存。桌面连接需要单独配置。</p>
      <button className="secondary" onClick={openConnections}>连接设置 <Icon name="arrow"/></button></div></section>
    <section className="guide-step"><span className="step-number">3</span><div><h3>创建会话，描述任务</h3>
      <p>进入 Agent 工作区，选择模型连接并点击「新建会话」。输入任务后按 Enter 发送，Shift + Enter 换行；任务运行时可以取消，完成后查看文件变化。</p></div></section>
    <section className="guide-step"><span className="step-number"><Icon name="shield"/></span><div><h3>发送按钮不可用时</h3>
      <p>先检查项目授权、模型连接和执行环境。当前开发版本的严格沙盒尚未完成原生验收，环境未就绪时会阻止任务启动；仍可浏览界面与项目。</p>
      <button className="secondary" onClick={openDiagnostics}>检查执行环境 <Icon name="arrow"/></button></div></section>
    <details className="guide-launch"><summary>下次如何启动客户端？</summary>
      <p>Windows：双击仓库根目录的 <code>Start-ForgeCode.cmd</code>。首次会构建界面，请等待窗口出现。</p>
      <p>也可以在仓库根目录的终端执行：</p><pre>npm run dev:desktop</pre>
      <p>完整说明：<code>docs/install/desktop-quickstart.md</code>。主题随系统自动切换；左下角始终可以打开这份使用指南。</p></details>
  </div>;
}
