# CDN CORS 配置说明

## 问题原因

你的字体 CDN `https://font.rainlodyn.cn/ibm-plex-sans-sc/` 缺少 CORS 头部，导致浏览器无法跨域加载字体文件。

### 检查命令：
```bash
curl -I https://font.rainlodyn.cn/ibm-plex-sans-sc/IBMPlexSansSC-Regular.woff2
```

**当前响应：** 没有 `Access-Control-Allow-Origin` 头部 ❌

## 解决方案

### 方案 1：Nginx 配置

在你的 Nginx 配置文件中添加：

```nginx
server {
    # ... 其他配置 ...

    location ~* \.(woff2?|ttf|otf|eot)$ {
        add_header Access-Control-Allow-Origin *;
        add_header Access-Control-Allow-Methods GET;
        add_header Access-Control-Allow-Headers Origin, Content-Type, Accept;
        # 如果有缓存配置，保持不变
    }
}
```

重启 Nginx：
```bash
sudo nginx -t  # 测试配置
sudo nginx -s reload  # 重载配置
```

### 方案 2：Apache 配置

在 `.htaccess` 或 Apache 配置文件中添加：

```apache
<IfModule mod_headers.c>
    <FilesMatch "\.(woff2?|ttf|otf|eot)$">
        Header set Access-Control-Allow-Origin "*"
    </FilesMatch>
</IfModule>
```

### 方案 3：Cloudflare

如果你的域名使用 Cloudflare：

1. 登录 Cloudflare 控制台
2. 选择你的域名
3. 进入 **Rules** → **Transform Rules** → **Modify Response Header**
4. 创建新规则：
   - **If incoming request matches**: `URI Path contains "/ibm-plex-sans-sc/"`
   - **Then**:
     - Set static header `Access-Control-Allow-Origin` to `*`

### 方案 4：宝塔面板

如果你使用宝塔面板：

1. 进入网站设置
2. 找到"配置文件"
3. 在 `server {}` 块内添加：

```nginx
location ~* \.(woff2?|ttf|otf|eot)$ {
    add_header Access-Control-Allow-Origin *;
}
```

4. 保存并重载配置

## 验证配置

配置完成后，再次检查：

```bash
curl -I https://font.rainlodyn.cn/ibm-plex-sans-sc/IBMPlexSansSC-Regular.woff2
```

应该看到：
```
HTTP/1.1 200 OK
Access-Control-Allow-Origin: *
...
```

## 使用你的 CDN

配置好 CORS 后，修改 HTML 文件：

**index.html:**
```html
<link rel="stylesheet" href="/ibm-plex-sans-sc.css">
```

**ibm-plex-sans-sc.css:**
```css
src: url('https://font.rainlodyn.cn/ibm-plex-sans-sc/IBMPlexSansSC-Regular.woff2') format('woff2');
```

## 当前临时方案

目前使用 Google Fonts CDN 作为临时方案，加载速度可能较慢。配置好你的 CDN CORS 后可以切换回去。

### 切换步骤：

1. 配置 CDN CORS 头部
2. 修改所有 HTML 文件，将 Google Fonts 链接替换为：
   ```html
   <link rel="stylesheet" href="/ibm-plex-sans-sc.css">
   ```
3. 重启服务器
4. 清除浏览器缓存测试

## 安全建议

如果只允许特定域名访问，可以将 `*` 替换为具体的域名：

```nginx
add_header Access-Control-Allow-Origin "https://yourdomain.com";
```

但对于字体文件这种公开资源，使用 `*` 是常见的做法。
