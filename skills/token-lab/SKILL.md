---
name: token-lab
description: Token haritası. Kullanıcının kendi Claude Code transcript'lerinden her token'ın nereye gittiğini ve hangilerinin hiç olmaması gerektiğini çıkarır; sadece /token-lab:token-lab ile çalışır.
disable-model-invocation: true
---

# Token haritası

Kullanıcının kendi kullanımını ölçen salt okunur bir rapor çalıştırırsın, sonucu sade dille anlatırsın. Hiçbir ayarı kendiliğinden değiştirmezsin.

## 1. Raporu çalıştır

Kullanıcının kural dosyası varsa (`~/.claude/token-lab.toml`) ekle:

```
uvx --from "${CLAUDE_PLUGIN_ROOT}" tokenlab why [--rules ~/.claude/token-lab.toml]
```

- `uvx` bulunamazsa kullanıcının dilinde "Önce uv'yi kur, sonra Claude'u yeniden aç." de, işletim sistemine göre komutu ver ve dur:
  - Windows: `winget install astral-sh.uv`
  - macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Rapor salt okur: `~/.claude/projects` altındaki transcript'ler, `~/.claude/agents`, `~/.claude/skills` ve transcript'te adı geçen `MEMORY.md` dosyaları. Büyük geçmişte bir dakika kadar sürer.
- Rapor ağa çıkmaz; yalnızca ilk çalıştırmada `uvx` paketi kurar, gerekirse Python indirir.
- Çıktının tamamını sohbete yapıştırma; okuyup özetle.

## 2. İlk görünüm: kısa ve her açı

Kullanıcının dilinde, teknik olmayan biri anlayacak şekilde. Raporun "Özet" ve "İlk adım" bölümleri bunun için yazıldı:

1. Raporun en üstteki iki kalın cümlesini rakamlarıyla söyle.
2. "Özet" tablosundaki her açı için tek satır, tablodaki sırayla: ne bulundu, maliyetteki payı, ne yapılır. "sorun yok" olanları sonda tek satırda topla. Teknik terimi bir kelimeyle açıkla (ör. önbellek, bağlam).
3. Ajanlar satırında general-purpose payını (ve varsa son günlerin trendini) söyle. 2. bölüm sonundaki "Ajan adayları" tablosundan hükmü "ajan adayı" olan işleri ve nerede çalıştıklarını (ana oturum ya da general-purpose) adıyla ver. 5. bölümden: "Recurring generic jobs" tablosundaki en büyük yeni ajan adayını, "Generic calls an existing agent covers" tablosundan mevcut bir ajanın kaçırdığı en büyük işi, "Existing agents" tablosundan bir hüküm (emekli et, birleştir, araçları daralt) varsa onu adıyla ver.
4. Cache ve hook satırları gözlemdir; kesin bir kazanç vaat etme.
5. Maliyetin "girdi-eşdeğeri token" olduğunu, abonelikte kotaya etkisinin ölçülmediğini bir kez belirt.
6. Sonda "İlk adım"ı söyle ve tek soru sor: "Bununla başlayalım mı?"

Çıktının tamamını sohbete yapıştırma; tabloları kullanıcı isterse aç.

## 3. Adım adım ilerlet

Her seferinde tek madde:

- Kullanıcı evet derse önce hedefi göster: hangi dosya ya da ayar, rapordaki kanıt, maliyetteki payı.
- Madde bitince ya da kullanıcı atlarsa, payı en büyük sıradaki maddeyi öner ve yine tek soru sor.
- Kullanıcı dur derse ya da kesilebilir madde kalmadıysa dur. Son cümle: "Bir hafta sonra `/token-lab:token-lab` ile yeniden çalıştır; uyguladıklarının payı düşmüş olmalı."

Bir dosyayı ya da ayarı değiştirmeden önce ilgili standardı oku ve ona göre yaz:

- Her değişiklikte: `${CLAUDE_PLUGIN_ROOT}/skills/token-lab/references/placement.md`. Bir satırın ya da kuralın nereye gideceğine karar tablosu ve "rapor bulgusu → yer" tablosuyla karar ver.
- Yeni ya da değişen ajan: `${CLAUDE_PLUGIN_ROOT}/skills/token-lab/references/agent.md`. 5. bölümdeki adayın araç ve model sütunlarını kullan.
- Hafıza dosyası ya da `MEMORY.md`: `${CLAUDE_PLUGIN_ROOT}/skills/token-lab/references/memory.md`.

Değiştirirken:

- Önce dokunacağın dosyayı ve tam farkı (diff) göster, onay al.
- Değiştirmeden önce dosyanın yedeğini aynı klasöre `.bak-token-lab-<tarih>` uzantısıyla al. Yedek, `settings.json`'daki `env` gibi gizli değerleri de taşır: içeriğini ekrana basma, kullanıcıya yalnızca yolunu söyle.
- `settings.json` gibi JSON dosyalarında yalnızca ilgili anahtarı ekle; dosyanın geri kalanına dokunma, `env` değerlerini ekrana basma.
- Plugin skill'lerini gizlemek için `skillOverrides` işlemez; o skill'in `SKILL.md` frontmatter'ına `disable-model-invocation: true` eklenir ve plugin güncellenince yeniden gerekir. Bunu kullanıcıya söyle.
- Hiçbir maddeyi kendiliğinden uygulama; her biri ayrı onay ister.

## 4. İsteğe bağlı: dosya okuması

Raporun 4. bölümündeki dosyaları kullanıcı isterse oku. Her satırı `references/placement.md`'deki altı soruyla değerlendir; global `~/.claude/CLAUDE.md` için altı koşulu da uygula. Her öneriyi satır numarası ve eylemle ver (sil, kısalt, taşı: nereye).

Bu kısım model yargısıdır; kullanıcıya öyle söyle. Silme ya da taşıma önerilerini 3. adımdaki gibi onayla uygula.

## 5. Bitiş

Son satırda, kullanıcının dilinde: "Sorun ya da öneri: https://github.com/merttrkr/claude-token-kit/issues — konuşma metni, dosya yolu ya da şirket bilgisi yapıştırmayın."
