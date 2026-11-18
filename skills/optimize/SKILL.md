---
name: optimize
description: Genel optimizasyon. Kullanıcının yüklenen CLAUDE.md, kural, hafıza, skill ve ajan dosyalarını placement standardına göre satır satır okuyup sil, kısalt, taşı ve ekle önerileri çıkarır; sadece /token-lab:optimize ile çalışır.
disable-model-invocation: true
---

# Genel optimizasyon

Kullanıcının bağlamına giren her parçayı placement standardına göre okur, satır satır öneri çıkarır ve onayla uygularsın. Rapordan (`/token-lab:token-lab`) farkı: rapor maliyeti ölçer, bu mod satırları okur. Token harcar; kapsamı kullanıcıyla belirlersin. Hiçbir dosyayı kendiliğinden değiştirmezsin.

## 1. Envanteri çıkar

Kullanıcının kural dosyası varsa (`~/.claude/token-lab.toml`) ekle:

```
uvx --from "${CLAUDE_PLUGIN_ROOT}" tokenlab inventory [--rules ~/.claude/token-lab.toml]
```

- Komut bir klasör yolu yazar. İçindeki `inventory.md`'yi oku; `system-prompt.md`'yi 3. adımda, `pieces.md`'yi (bütün parçalar) kapsam genişlerse kullanırsın.
- `inventory.md` "Henüz hiç Claude Code isteği yok" diyorsa bunu kullanıcıya söyle ve dur.
- `uvx` bulunamazsa kullanıcının dilinde "Önce uv'yi kur, sonra Claude'u yeniden aç." de, işletim sistemine göre komutu ver ve dur (Windows: `winget install astral-sh.uv`; macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`).
- Envanter salt okur ve ağa çıkmaz. Bölümleri:
  1. **Parçalar:** her parçanın dosyası, boyu, haftalık maliyeti, kullanımı.
  2. **Eskimiş referanslar:** diskte olmayan yollar. Betik sonucu, kesin.
  3. **Tekrar eden satırlar:** system prompt'un zaten söylediği cümleler ve aynı cümleleri paylaşan dosya çiftleri. Betik sonucu, kesin.
  4. **Eklenebilecekler:** tekrar eden araç hataları, oturumlar arasında tekrar okunan dosyalar, düzeltme mesajlarının yerleri. Bunlar aday; kararı okuma verir.

## 2. Kapsamı belirle

Kullanıcının dilinde, kısa:

1. Kaç parça var, en pahalı 10'u hangisi (1. bölüm), 2–3. bölümde kaç kesin bulgu, 4. bölümde kaç aday var.
2. Tahmini maliyet, kabaca ve alt sınır olarak: okunacak parçaların karakteri, artı her okuyucu için `placement.md` ve `system-prompt.md` (ajan sayısı kadar tekrar eder), artı 4. bölüm için transcript kesitleri; toplamı ÷ 4 ≈ token, çıktı ayrıca. Rakamı söyle.
3. Tek soru, iki parçalı: "En pahalı 10 parça ve kesin bulgularla başlayayım mı? Eklenebilecekler için transcript'lerinden örnek yerleri de okuyayım mı?" Kullanıcı genişletebilir (`pieces.md`'den), daraltabilir ya da transcript okumasını reddedebilir; 4. adımı sadece evet derse yap.

Plugin'den gelen parçalar (`~/.claude/plugins/cache/...` altında) plugin güncellenince eski hâline döner. Onlar için düzenleme yerine gizleme ya da kapatma öner ve bunu kullanıcıya söyle.

## 3. Oku ve değerlendir

Önce `${CLAUDE_PLUGIN_ROOT}/skills/token-lab/references/placement.md`'yi oku. Sonra kapsamdaki her parça için:

- Dosyayı oku. Her satırı ya da bloğu standarttaki altı soruyla değerlendir; global `~/.claude/CLAUDE.md` için altı koşulu da uygula.
- "Claude zaten yapıyor mu?" sorusunu `system-prompt.md`'ye bakarak cevapla: aynı şey orada yazıyorsa satır gereksizdir. Orada yoksa kendi bildiğin varsayılana dayanma; "doğrulanamadı" de.
- Envanterin 2. ve 3. bölümündeki satırları olgu olarak kullan, yeniden tartışma.
- Kapsamda 3'ten fazla parça varsa okumayı paralel, salt okunur ajanlara böl (parça başına ya da küçük gruplar). Her ajana parçanın yolunu, `placement.md` ve `system-prompt.md` yollarını ve envanterde o dosyaya ait satırları ver. Ajanlar dosya değiştirmez, sadece aşağıdaki tabloyu döndürür.

Okumalar bitince sen, ana oturumda, gelen tabloları birlikte oku: dosyalar arasında birbirini tutmayan kurallar ve aynı şeyi başka sözlerle söyleyen satırlar (betik sadece birebir aynı cümleleri bulur) için bulgu ekle.

Her bulgu için bir satır:

| parça:satır | ne | geçemediği soru | eylem (sil / kısalt / taşı / birleştir) | nereye | kesinlik (kesin: betik / yargı: model) |
|---|---|---|---|---|---|

## 4. Eklenebilecekleri değerlendir

- **Tekrar eden araç hataları:** envanterdeki örnek yerleri aç (transcript dosyasında o zamanı ara), hatayı ve onu üreten komutu gör. Makinenin kontrol edebileceği bir kalıp tekrar ediyorsa hook ya da izin kuralı öner (placement tablosundaki hook satırı); değilse doğru yerde tek satırlık bir kural.
- **Oturumlar arasında tekrar okunan dosyalar:** her oturumda gerekiyorsa hafıza satırı ya da CLAUDE.md'de bir işaret ("X'ten önce Y'yi oku"); sadece belli bir işte gerekiyorsa o işin skill'ine referans.
- **Düzeltmeler:** listelenen her mesajı transcript'inde, önündeki Claude cevabıyla birlikte oku. Tekrar eden düzeltmeleri grupla ve tetiklendiği en ucuz yere bir kural öner. Tek seferlik olanlara bir şey ekleme.
- Transcript metnini hiçbir yere kopyalama; sadece öneride kısaca özetle.

## 5. Tek liste göster

- Tüm bulguları tek listede birleştir. Sıra: maliyet etkisi (parçanın haftalık maliyeti × etkilenen satırların payı); eşitlikte kesin bulgular önce.
- İlk 10'u sade dille göster: ne, neden, nereye gidiyor, etkisi. Teknik terimi bir kelimeyle açıkla.
- Tek soru: "Birinciyle başlayalım mı?"

## 6. Adım adım uygula

Her seferinde tek öneri, her biri ayrı onayla:

- Önce dokunacağın dosyayı ve tam farkı (diff) göster, onay al.
- Değiştirmeden önce dosyanın yedeğini aynı klasöre `.bak-token-lab-<YYYYMMDD-HHMMSS>` uzantısıyla al; aynı adda yedek varsa üzerine yazma, yeni ad seç. Yedek, `settings.json`'daki `env` gibi gizli değerleri de taşır: içeriğini ekrana basma, kullanıcıya yalnızca yolunu söyle.
- `settings.json` için tam dosya farkı gösterme: sadece değişen anahtarın satırlarını göster; `env` ve benzeri değerleri `***` ile maskele.
- Taşımada önce hedefe ekle, sonra kaynaktan sil; ikisini aynı farkta göster.
- `settings.json` gibi JSON dosyalarında yalnızca ilgili anahtarı ekle; dosyanın geri kalanına dokunma.
- Yeni ya da değişen ajan için `${CLAUDE_PLUGIN_ROOT}/skills/token-lab/references/agent.md`, hafıza için `${CLAUDE_PLUGIN_ROOT}/skills/token-lab/references/memory.md` kurallarına uy.
- Madde bitince ya da kullanıcı atlarsa sıradakini öner. Kullanıcı dur derse ya da öneri kalmadıysa dur.

## 7. Bitiş

- Envanter klasörünü sil (system prompt'u ve dosya yollarını taşır) ve kullanıcıya silindiğini söyle.

Son iki cümle, kullanıcının dilinde:

- "Bir hafta sonra `/token-lab:token-lab` ile raporu yeniden çalıştır; değiştirdiğin parçaların maliyeti düşmüş olmalı."
- "Sorun ya da öneri: https://github.com/merttrkr/claude-token-kit/issues — konuşma metni, dosya yolu ya da şirket bilgisi yapıştırmayın."
