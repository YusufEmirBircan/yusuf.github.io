const canvas = document.getElementById('particleCanvas');
const ctx = canvas.getContext('2d');

let dpr = window.devicePixelRatio || 1;
let lastWidth = window.innerWidth;

function setupCanvas() {
    canvas.width = window.innerWidth * dpr;
    canvas.height = window.innerHeight * dpr;
    canvas.style.width = window.innerWidth + 'px';
    canvas.style.height = window.innerHeight + 'px';
    ctx.scale(dpr, dpr);
}

setupCanvas();

let particles = [];
const mouse = { x: null, y: null, radius: 100 };

window.addEventListener('mousemove', (event) => {
    mouse.x = event.x;
    mouse.y = event.y;
});

window.addEventListener('mouseout', () => {
    mouse.x = null;
    mouse.y = null;
});


window.addEventListener('touchstart', (event) => {

    mouse.x = event.touches[0].clientX;
    mouse.y = event.touches[0].clientY;
});

window.addEventListener('touchmove', (event) => {
    mouse.x = event.touches[0].clientX;
    mouse.y = event.touches[0].clientY;
});

window.addEventListener('touchend', () => {
    mouse.x = null;
    mouse.y = null;
});


function getTextPoints(text) {
    const offCanvas = document.createElement('canvas');
    const offCtx = offCanvas.getContext('2d', { willReadFrequently: true });

    offCanvas.width = window.innerWidth;
    offCanvas.height = window.innerHeight;

    offCtx.fillStyle = 'white';
    let fontSize = Math.min(window.innerWidth * 0.1, 90);
    if (window.innerWidth < 600) fontSize = Math.max(fontSize, 50);
    offCtx.font = `bold ${fontSize}px "Arial", sans-serif`;

    offCtx.textAlign = 'center';
    offCtx.textBaseline = 'middle';

    offCtx.fillText(text, window.innerWidth / 2, window.innerHeight / 2);

    const pixels = offCtx.getImageData(0, 0, offCanvas.width, offCanvas.height).data;
    const points = [];


    const gap = (window.innerWidth < 600) ? 1.5 : 3;

    for (let y = 0; y < offCanvas.height; y += gap) {
        for (let x = 0; x < offCanvas.width; x += gap) {
            const index = (Math.floor(y) * offCanvas.width + Math.floor(x)) * 4;
            const alpha = pixels[index + 3];

            if (alpha > 128) {
                points.push({ x: x, y: y });
            }
        }
    }
    return points;
}

class Particle {
    constructor(targetX, targetY) {
        this.targetX = targetX;
        this.targetY = targetY;


        this.x = Math.random() * window.innerWidth;
        this.y = Math.random() * window.innerHeight;
        this.size = Math.random() * 0.7 + 0.4;


        this.vx = (Math.random() - 0.5) * 2;
        this.vy = (Math.random() - 0.5) * 2;

        this.active = false;
        this.framesSinceLeft = 0;


        this.alpha = Math.random() * Math.PI * 2;
        this.twinkleSpeed = 0.01047;
    }

    draw() {

        const baseOpacity = Math.abs(Math.sin(this.alpha));
        const opacity = 0.1 + Math.pow(baseOpacity, 4) * 0.9;
        ctx.fillStyle = `rgba(255, 255, 255, ${opacity})`;

        ctx.beginPath();
        ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
        ctx.closePath();
        ctx.fill();
    }

    update() {

        this.alpha += this.twinkleSpeed;

        let distanceToMouse = 9999;

        if (mouse.x !== null && mouse.y !== null) {

            let dx = mouse.x - this.targetX;
            let dy = mouse.y - this.targetY;
            distanceToMouse = Math.sqrt(dx * dx + dy * dy);
        }


        if (distanceToMouse < mouse.radius) {
            this.active = true;
            this.framesSinceLeft = 0;
        } else {

            if (this.active) {
                this.framesSinceLeft++;
                if (this.framesSinceLeft > 30000) {
                    this.active = false;
                    this.framesSinceLeft = 0;
                }
            }
        }

        if (this.active) {

            let time = Date.now() * 0.002;
            let breathingX = Math.sin(time + (this.targetX * 0.01)) * 1.5;
            let breathingY = Math.cos(time + (this.targetY * 0.01)) * 1.5;


            let tx = (this.targetX + breathingX) - this.x;
            let ty = (this.targetY + breathingY) - this.y;

            this.vx += tx * 0.05;
            this.vy += ty * 0.05;
            this.vx *= 0.8;
            this.vy *= 0.8;

            this.x += this.vx;
            this.y += this.vy;
        } else {

            this.vx += (Math.random() - 0.5) * 0.05;
            this.vy += (Math.random() - 0.5) * 0.05;


            let speed = Math.sqrt(this.vx * this.vx + this.vy * this.vy);
            if (speed > 0.6) {
                this.vx = (this.vx / speed) * 0.6;
                this.vy = (this.vy / speed) * 0.6;
            }

            this.x += this.vx;
            this.y += this.vy;


            if (this.x < 0 || this.x > canvas.width) this.vx *= -1;
            if (this.y < 0 || this.y > canvas.height) this.vy *= -1;
        }
    }
}

function init() {

    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;

    particles = [];
    const textPoints = getTextPoints("Yusuf Emir");
    textPoints.forEach(p => {
        particles.push(new Particle(p.x, p.y));
    });
}

function animate() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    for (let i = 0; i < particles.length; i++) {
        particles[i].draw();
        particles[i].update();
    }
    requestAnimationFrame(animate);
}

init();
animate();


let resizeTimer;
window.addEventListener('resize', () => {


    if (Math.abs(window.innerWidth - lastWidth) < 10) return;

    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
        lastWidth = window.innerWidth;
        setupCanvas();
        init();
    }, 200);
});

// Haberleri news.json İçinden Yükleme
document.addEventListener('DOMContentLoaded', () => {
    fetchAnnouncement();
    fetchNews();
});

// Duyuru Çubuğu Mantığı
async function fetchAnnouncement() {
    try {
        const response = await fetch('announcement.json');
        if (!response.ok) return;
        const data = await response.json();
        const tickerBar = document.getElementById('ticker-bar');
        const tickerText = document.getElementById('tickerText');
        
        if (data.announcement && data.announcement.trim() !== '') {
            tickerText.textContent = data.announcement;
            tickerBar.style.display = 'block';
            document.body.classList.add('has-ticker');
            initSmoothTicker(tickerText);
        } else {
            tickerBar.style.display = 'none';
            document.body.classList.remove('has-ticker');
        }
    } catch (error) {
        console.log("Duyuru dosyası yüklenemedi:", error);
    }
}

function initSmoothTicker(el) {
    // Başlangıç pozisyonu ekranın sağından başlar
    let pos = window.innerWidth;
    let speed = 0.8;          // Normal hız (px/frame)
    let currentSpeed = speed;
    let targetSpeed = speed;
    const acceleration = 0.03; // Yumuşak geçiş hızı
    let animFrameId;

    function animate() {
        // Hedefe doğru yumuşakça yaklaş
        currentSpeed += (targetSpeed - currentSpeed) * acceleration;
        pos -= currentSpeed;

        // Yazı tamamen sol kenardan çıktıysa sağdan tekrar başlat
        if (pos < -el.offsetWidth) {
            pos = window.innerWidth;
        }

        el.style.transform = `translateX(${pos}px)`;
        el.style.animation = 'none'; // CSS animasyonunu devre dışı bırak
        animFrameId = requestAnimationFrame(animate);
    }

    const tickerBar = el.closest('.ticker-bar');
    tickerBar.addEventListener('mouseenter', () => {
        targetSpeed = 0; // Yavaşça dur
    });
    tickerBar.addEventListener('mouseleave', () => {
        targetSpeed = speed; // Yavaşça devam et
    });

    animate();
}

window.allNewsList = [];

function fetchNews() {
    fetch('news.json')
        .then(response => response.json())
        .then(data => {
            window.allNewsList = data;
            renderNews(data);
        })
        .catch(err => {
            console.error('Haberler yüklenirken hata oluştu:', err);
        });
}

function escapeHTML(str) {
    if (!str) return '';
    return str.toString()
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function renderNews(newsList) {
    const grid = document.getElementById('newsGrid');
    if (!grid) return;

    if (!newsList || newsList.length === 0) {
        grid.innerHTML = '<p style="color: #666;">Henüz yayınlanmış bir haber bulunmuyor.</p>';
        return;
    }

    grid.innerHTML = newsList.map(item => `
        <div class="news-card" onclick="window.open('${escapeHTML(item.sourceUrl)}', '_blank')">
            <img src="${escapeHTML(item.image) || 'https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?auto=format&fit=crop&w=800&q=80'}" alt="${escapeHTML(item.title)}" class="news-image">
            <div class="news-body">
                <span class="news-date">${escapeHTML(item.category || 'Diğer')} • ${escapeHTML(item.date) || ''}</span>
                <h3 class="news-title">${escapeHTML(item.title)}</h3>
                <p class="news-summary">${escapeHTML(item.summary)}</p>
                <span class="news-read-more">Devamını Oku ↗</span>
            </div>
        </div>
    `).join('');

    window.currentNewsList = newsList;
}


document.addEventListener('DOMContentLoaded', () => {
    const filterBtns = document.querySelectorAll('.cat-btn');
    filterBtns.forEach(btn => {
        btn.addEventListener('click', (e) => {
            filterBtns.forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');
            
            const selectedCategory = e.target.getAttribute('data-cat');
            
            if (selectedCategory === 'Tümü') {
                renderNews(window.allNewsList);
            } else {
                const filtered = window.allNewsList.filter(news => news.category === selectedCategory);
                renderNews(filtered);
            }
        });
    });

    // Dropdown Menü Aç/Kapat Mantığı
    const menuToggle = document.getElementById('menuToggle');
    const menuDropdown = document.getElementById('menuDropdown');

    if (menuToggle && menuDropdown) {
        menuToggle.addEventListener('click', (e) => {
            e.stopPropagation(); // Tıklamayı durdur ki body click hemen kapatmasın
            menuDropdown.classList.toggle('active');
        });

        // Dropdown dışına tıklanırsa kapat
        document.addEventListener('click', (e) => {
            if (!menuToggle.contains(e.target) && !menuDropdown.contains(e.target)) {
                menuDropdown.classList.remove('active');
            }
        });

        // Menüdeki bir linke tıklayınca menüyü otomatik kapat
        const menuLinks = menuDropdown.querySelectorAll('a');
        menuLinks.forEach(link => {
            link.addEventListener('click', () => {
                menuDropdown.classList.remove('active');
            });
        });
    }
});

