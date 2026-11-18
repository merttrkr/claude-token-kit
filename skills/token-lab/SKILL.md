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

- `uvx` bulunamazsa: "Önce uv'yi kur: `winget install astral-sh.uv`, sonra Claude'u yeniden aç." de ve dur.
- Rapor salt okur: `~/.claude/projects` altındaki transcript'ler, `~/.claude/agents`, `~/.claude/skills` ve transcript'te adı geçen `MEMORY.md` dosyaları. Büyük geçmişte bir dakika kadar sürer.
- Rapor ağa çıkmaz; yalnızca ilk çalıştırmada `uvx` paketi kurar, gerekirse Python indirir.
- Çıktının tamamını sohbete yapıştırma; okuyup özetle.

## 2. Sade özet ver

Kullanıcının dilinde, teknik olmayan biri anlayacak şekilde:

1. Tek cümle: son N günde istem token'larının yüzde kaçı her istekte tekrar giden kısım (raporun 1. bölümü, "toplam" satırı).
2. 2. bölümden maliyeti en büyük **en fazla 5 madde**. Her biri için: ne bulundu (rakamla), neden israf, ne yapılır. Kod adlarını (T, S, A, M, R, C) kullanma.
3. Rapordaki "zaten düzelmiş görünüyor" satırı varsa bir cümleyle söyle.
4. Maliyetin "girdi-eşdeğeri token" olduğunu, abonelikte kotaya etkisinin ölçülmediğini bir kez belirt.

## 3. Kullanıcı isterse uygula

Kullanıcı bir maddeyi uygulamanı isterse:

- Önce dokunacağın dosyayı ve tam farkı (diff) göster, onay al.
- Değiştirmeden önce dosyanın yedeğini aynı klasöre `.bak-token-lab-<tarih>` uzantısıyla al.
- `settings.json` gibi JSON dosyalarında yalnızca ilgili anahtarı ekle; dosyanın geri kalanına dokunma, `env` değerlerini ekrana basma.
- Plugin skill'lerini gizlemek için `skillOverrides` işlemez; o skill'in `SKILL.md` frontmatter'ına `disable-model-invocation: true` eklenir ve plugin güncellenince yeniden gerekir. Bunu kullanıcıya söyle.
- Hiçbir maddeyi kendiliğinden uygulama; her biri ayrı onay ister.

## 4. İsteğe bağlı: dosya okuması

Raporun 4. bölümündeki dosyaları kullanıcı isterse oku ve her biri için şu soruları cevapla, satır numarasıyla:

- Bu satır davranışı değiştiriyor mu, yoksa Claude zaten öyle mi yapıyor?
- Doğru okuyucuya mı gidiyor (her istekte mi gerekli, yoksa sadece belli bir işte mi)?
- Doğru yerde mi (CLAUDE.md, klasör kuralı, skill, hafıza)?
- Senin kendi varsayılan talimatlarını mı tekrar ediyor?

Bu kısım model yargısıdır; kullanıcıya öyle söyle. Silme ya da taşıma önerilerini 3. adımdaki gibi onayla uygula.

## 5. Bitiş

Son satırda: "Sorun ya da öneri: https://github.com/merttrkr/claude-token-kit/issues — konuşma metni, dosya yolu ya da şirket bilgisi yapıştırmayın."
