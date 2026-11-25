import React, { useState } from 'react';
import { Bookmark, X, Check } from 'lucide-react';
import { ViralClip } from '../types';

interface BrandKitModalProps {
  isOpen: boolean;
  onClose: () => void;
  activeClip: ViralClip | null;
  safeZoneGuide: string;
  onSaveBrandKit: (name: string) => Promise<void>;
}

export const BrandKitModal: React.FC<BrandKitModalProps> = ({
  isOpen,
  onClose,
  activeClip,
  safeZoneGuide,
  onSaveBrandKit
}) => {
  const [kitName, setKitName] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [success, setSuccess] = useState(false);

  if (!isOpen) return null;

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!kitName.trim()) return;
    setIsSaving(true);
    try {
      await onSaveBrandKit(kitName.trim());
      setSuccess(true);
      setTimeout(() => {
        setSuccess(false);
        onClose();
      }, 1200);
    } catch (err) {
      console.error('Failed to save brand kit:', err);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-2xl w-full max-w-md p-5 shadow-2xl text-xs space-y-4 animate-fadeIn text-[var(--text-primary)]">
        <div className="flex items-center justify-between border-b border-[var(--border-subtle)] pb-3">
          <div className="flex items-center gap-2">
            <Bookmark className="w-4 h-4 text-[var(--accent-primary)]" />
            <h3 className="font-bold text-[var(--text-primary)] text-sm">Save Brand Kit</h3>
          </div>
          <button onClick={onClose} className="text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
            <X className="w-4 h-4" />
          </button>
        </div>

        <p className="text-[var(--text-secondary)] text-xs">
          Save your current caption style (<strong className="text-[var(--text-primary)]">{activeClip?.caption_style}</strong>), colors, format (<strong className="text-[var(--text-primary)]">{activeClip?.aspect_ratio}</strong>), watermark corner, and platform guide (<strong className="text-[var(--text-primary)]">{safeZoneGuide}</strong>) as a reusable brand preset.
        </p>

        <form onSubmit={handleSave} className="space-y-4">
          <div>
            <label className="text-[11px] font-semibold text-[var(--text-primary)] block mb-1">
              Brand Kit Name
            </label>
            <input
              type="text"
              required
              placeholder="e.g. Creator Dark Obsidian"
              value={kitName}
              onChange={(e) => setKitName(e.target.value)}
              className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-3 py-2 text-[var(--text-primary)] focus:border-[var(--accent-primary)] outline-none"
              autoFocus
            />
          </div>

          <div className="flex justify-end gap-2 pt-2 border-t border-[var(--border-subtle)]">
            <button
              type="button"
              onClick={onClose}
              className="px-3 py-1.5 rounded-lg border border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSaving || !kitName.trim()}
              className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg bg-[var(--accent-primary)] hover:opacity-90 text-[var(--accent-foreground)] font-bold transition disabled:opacity-50"
            >
              {success ? <Check className="w-3.5 h-3.5" /> : null}
              <span>{success ? 'Saved!' : isSaving ? 'Saving...' : 'Save Kit'}</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
