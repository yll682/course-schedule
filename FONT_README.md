# IBM Plex Sans SC 字体使用说明

## 字体文件配置

### 1. 需要的字重文件（推荐）

将以下 4 个 `.woff2` 文件放到项目根目录：

```
IBMPlexSansSC-Regular.woff2     (400 - 常规文本)
IBMPlexSansSC-Medium.woff2      (500 - 中等强调)
IBMPlexSansSC-SemiBold.woff2    (600 - 标题)
IBMPlexSansSC-Bold.woff2        (700 - 粗体标题)
```

### 2. 可删除的文件（不推荐）

以下字重对于课程表应用不太需要，可以删除以减少加载时间：

```
IBMPlexSansSC-ExtraLight.woff2  (太细，打印不清晰)
IBMPlexSansSC-Light.woff2       (较细，打印不清晰)
IBMPlexSansSC-Text.woff2        (和 Regular 太接近)
IBMPlexSansSC-Thin.woff2        (太细，打印不清晰)
```

### 3. 文件大小估算

每个 `.woff2` 文件大约：
- Regular: ~800KB
- Medium: ~800KB
- SemiBold: ~800KB
- Bold: ~800KB
- **总计：约 3.2MB**

相比完整字重包（10MB+），节省了约 70% 的体积。

## 使用方法

### 本地开发
字体文件已经在 `ibm-plex-sans-sc.css` 中配置好，只需：
1. 将 4 个 `.woff2` 文件复制到项目根目录
2. 刷新页面即可

### 生产部署
如果后续使用 CDN（如 `font.example.com`）：

1. 将字体文件上传到 CDN
2. 修改 `ibm-plex-sans-sc.css` 中的 URL：

```css
@font-face {
    font-family: 'IBM Plex Sans SC';
    src: url('https://font.example.com/IBMPlexSansSC-Regular.woff2') format('woff2');
    font-weight: 400;
    font-style: normal;
    font-display: swap;
}
/* ... 其他字重同理 */
```

## 为什么选择这些字重？

### Regular (400)
- 用于：正文、标签、次要信息
- 示例：课程地点、教师姓名

### Medium (500)
- 用于：略微强调的文本
- 示例：按钮文字、提示信息

### SemiBold (600)
- 用于：标题、重要信息
- 示例：星期标题、节次编号

### Bold (700)
- 用于：强调标题、课程名称
- 示例：课程名称、页面标题

## 为什么不用更细的字重？

- **打印清晰度**：Light/Thin 字重在打印时线条太细，容易模糊
- **屏幕对比度**：细字重在低对比度环境下难以阅读
- **加载速度**：每个字重约 800KB，减少不必要的字重可加快页面加载
- **实际需求**：课程表应用主要需要清晰的标题和正文，不需要装饰性的细字重

## 测试字体加载

在浏览器控制台运行：

```javascript
// 检查字体是否加载成功
document.fonts.ready.then(() => {
    const font = document.fonts.check('16px "IBM Plex Sans SC"');
    console.log('IBM Plex Sans SC 加载:', font ? '成功' : '失败');
});
```

## 故障排查

如果字体未加载：
1. 检查文件是否在正确路径（项目根目录）
2. 检查文件名是否正确（区分大小写）
3. 检查浏览器控制台是否有 404 错误
4. 清除浏览器缓存后重试
