"use client";

import Image from "next/image";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowUp, ImagePlus, LoaderCircle, MessageSquarePlus, Trash2, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { fetchAccounts, generateImage, editImage, fetchImageTask, type Account, type ImageModel } from "@/lib/api";
import {
  clearImageConversations,
  deleteImageConversation,
  listImageConversations,
  saveImageConversation,
  type ImageConversation,
  type StoredImage,
} from "@/store/image-conversations";
import { cn } from "@/lib/utils";

const imageModelOptions: Array<{ label: string; value: ImageModel }> = [
  { label: "gpt-image-1", value: "gpt-image-1" },
  { label: "gpt-image-2", value: "gpt-image-2" },
];

function buildConversationTitle(prompt: string) {
  const trimmed = prompt.trim();
  if (trimmed.length <= 5) {
    return trimmed;
  }
  return `${trimmed.slice(0, 5)}...`;
}

function formatConversationTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatAvailableQuota(accounts: Account[]) {
  const availableAccounts = accounts.filter((account) => account.status !== "禁用");
  return String(availableAccounts.reduce((sum, account) => sum + Math.max(0, account.quota), 0));
}

async function normalizeConversationHistory(items: ImageConversation[]) {
  const normalized = items.map((item) =>
    item.status === "generating"
      ? {
          ...item,
          status: "error" as const,
          error: item.images.some((image) => image.status === "success")
            ? item.error || "生成已中断"
            : "页面已刷新，生成已中断",
          images: item.images.map((image) =>
            image.status === "loading"
              ? {
                  ...image,
                  status: "error" as const,
                  error: "页面已刷新，生成已中断",
                }
              : image,
          ),
        }
      : item,
  );

  await Promise.all(
    normalized
      .filter((item, index) => item !== items[index])
      .map((item) => saveImageConversation(item)),
  );

  return normalized;
}

export default function ImagePage() {
  const didLoadQuotaRef = useRef(false);
  const [imagePrompt, setImagePrompt] = useState("");
  const [imageCount, setImageCount] = useState("1");
  const [imageModel, setImageModel] = useState<ImageModel>("gpt-image-1");
  const [conversations, setConversations] = useState<ImageConversation[]>([]);
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null);
  const [isLoadingHistory, setIsLoadingHistory] = useState(true);
  const [isGenerating, setIsGenerating] = useState(false);
  const [availableQuota, setAvailableQuota] = useState("加载中");
  const [referenceImages, setReferenceImages] = useState<File[]>([]);
  const [referencePreviews, setReferencePreviews] = useState<string[]>([]);
  const resultsViewportRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const parsedCount = useMemo(() => Math.max(1, Math.min(10, Number(imageCount) || 1)), [imageCount]);
  const selectedConversation = useMemo(
    () => conversations.find((item) => item.id === selectedConversationId) ?? null,
    [conversations, selectedConversationId],
  );

  useEffect(() => {
    let cancelled = false;

    const loadHistory = async () => {
      try {
        const items = await listImageConversations();
        const normalizedItems = await normalizeConversationHistory(items);
        if (cancelled) {
          return;
        }
        setConversations(normalizedItems);
      } catch (error) {
        const message = error instanceof Error ? error.message : "读取会话记录失败";
        toast.error(message);
      } finally {
        if (!cancelled) {
          setIsLoadingHistory(false);
        }
      }
    };

    void loadHistory();
    return () => {
      cancelled = true;
    };
  }, []);

  const loadQuota = useCallback(async () => {
    try {
      const data = await fetchAccounts();
      setAvailableQuota(formatAvailableQuota(data.items));
    } catch {
      setAvailableQuota((prev) => (prev === "加载中" ? "—" : prev));
    }
  }, []);

  useEffect(() => {
    if (didLoadQuotaRef.current) {
      return;
    }
    didLoadQuotaRef.current = true;

    const syncQuota = async () => {
      await loadQuota();
    };

    const handleFocus = () => {
      void syncQuota();
    };

    void syncQuota();
    window.addEventListener("focus", handleFocus);
    return () => {
      window.removeEventListener("focus", handleFocus);
    };
  }, [loadQuota]);

  useEffect(() => {
    if (!selectedConversation && !isGenerating) {
      return;
    }

    resultsViewportRef.current?.scrollTo({
      top: resultsViewportRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [selectedConversation, isGenerating]);

  const persistConversation = async (conversation: ImageConversation) => {
    setConversations((prev) => {
      const next = [conversation, ...prev.filter((item) => item.id !== conversation.id)];
      return next.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
    });
    await saveImageConversation(conversation);
  };

  const updateConversation = async (
    conversationId: string,
    updater: (current: ImageConversation | null) => ImageConversation,
  ) => {
    let nextConversation: ImageConversation | null = null;

    setConversations((prev) => {
      const current = prev.find((item) => item.id === conversationId) ?? null;
      nextConversation = updater(current);
      const next = [nextConversation, ...prev.filter((item) => item.id !== conversationId)];
      return next.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
    });

    if (nextConversation) {
      await saveImageConversation(nextConversation);
    }
  };

  const clearReferenceImages = () => {
    referencePreviews.forEach((url) => URL.revokeObjectURL(url));
    setReferenceImages([]);
    setReferencePreviews([]);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const handleFileSelect = (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const newFiles: File[] = [];
    const newPreviews: string[] = [];
    for (const file of Array.from(files)) {
      if (!file.type.startsWith("image/")) {
        toast.error(`${file.name} 不是图片文件`);
        continue;
      }
      if (file.size > 20 * 1024 * 1024) {
        toast.error(`${file.name} 超过 20MB`);
        continue;
      }
      newFiles.push(file);
      newPreviews.push(URL.createObjectURL(file));
    }
    if (newFiles.length > 0) {
      setReferenceImages((prev) => [...prev, ...newFiles]);
      setReferencePreviews((prev) => [...prev, ...newPreviews]);
    }
  };

  const removeReferenceImage = (index: number) => {
    URL.revokeObjectURL(referencePreviews[index]);
    setReferenceImages((prev) => prev.filter((_, i) => i !== index));
    setReferencePreviews((prev) => prev.filter((_, i) => i !== index));
  };

  const handleCreateDraft = () => {
    setSelectedConversationId(null);
    setImagePrompt("");
    clearReferenceImages();
    textareaRef.current?.focus();
  };

  const handleDeleteConversation = async (id: string) => {
    const nextConversations = conversations.filter((item) => item.id !== id);
    setConversations(nextConversations);
    setSelectedConversationId((prev) => (prev === id ? null : prev));

    try {
      await deleteImageConversation(id);
    } catch (error) {
      const message = error instanceof Error ? error.message : "删除会话失败";
      toast.error(message);
      const items = await listImageConversations();
      setConversations(items);
    }
  };

  const handleClearHistory = async () => {
    try {
      await clearImageConversations();
      setConversations([]);
      setSelectedConversationId(null);
      toast.success("已清空历史记录");
    } catch (error) {
      const message = error instanceof Error ? error.message : "清空历史记录失败";
      toast.error(message);
    }
  };

  const handleGenerateImage = async () => {
    const prompt = imagePrompt.trim();
    if (!prompt) {
      toast.error("请输入提示词");
      return;
    }

    const now = new Date().toISOString();
    const conversationId =
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const draftConversation: ImageConversation = {
      id: conversationId,
      title: buildConversationTitle(prompt),
      prompt,
      model: imageModel,
      count: parsedCount,
      images: Array.from({ length: parsedCount }, (_, index) => ({
        id: `${conversationId}-${index}`,
        status: "loading" as const,
      })),
      createdAt: now,
      status: "generating",
    };

    setIsGenerating(true);
    setSelectedConversationId(conversationId);
    setImagePrompt("");

    // Capture reference images before clearing
    const currentRefImages = [...referenceImages];
    const currentRefPreviews = [...referencePreviews];
    clearReferenceImages();

    // Convert reference images to base64 data URLs for storage
    let refImageDataUrls: string[] = [];
    if (currentRefImages.length > 0) {
      for (const file of currentRefImages) {
        try {
          const dataUrl = await new Promise<string>((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result as string);
            reader.onerror = reject;
            reader.readAsDataURL(file);
          });
          refImageDataUrls.push(dataUrl);
        } catch {
          // skip failed conversions
        }
      }
    }

    // Update draft with reference images
    if (refImageDataUrls.length > 0) {
      draftConversation.referenceImage = refImageDataUrls[0];
      (draftConversation as any).referenceImages = refImageDataUrls;
    }

    try {
      await persistConversation(draftConversation);

      const tasks = Array.from({ length: parsedCount }, async (_, index) => {
        try {
          let data = currentRefImages.length > 0
            ? await editImage(prompt, currentRefImages, imageModel)
            : await generateImage(prompt, imageModel);

          // Handle async task — poll until completed
          if (data.task_id && !data.data?.length) {
            const taskId = data.task_id;
            toast.info("图片已加入排队，正在等待生成...");
            const maxPollTime = 600_000; // 10 minutes
            const pollInterval = 5_000; // 5 seconds
            const startTime = Date.now();

            while (Date.now() - startTime < maxPollTime) {
              await new Promise((resolve) => setTimeout(resolve, pollInterval));
              const taskResult = await fetchImageTask(taskId);
              if (taskResult.status === "completed" && taskResult.result) {
                data = taskResult.result;
                break;
              }
              if (taskResult.status === "failed") {
                throw new Error(taskResult.error || "图片生成失败");
              }
            }

            if (!data.data?.length) {
              throw new Error("图片生成超时，请稍后在任务列表中查看");
            }
          }

          const first = data.data?.[0];
          if (!first?.b64_json) {
            throw new Error(`第 ${index + 1} 张没有返回图片数据`);
          }

          const nextImage: StoredImage = {
            id: `${conversationId}-${index}`,
            status: "success",
            b64_json: first.b64_json,
          };

          await updateConversation(conversationId, (current) => ({
            ...(current ?? draftConversation),
            images: (current?.images ?? draftConversation.images).map((image) =>
              image.id === nextImage.id ? nextImage : image,
            ),
          }));

          return nextImage;
        } catch (error) {
          const message = error instanceof Error ? error.message : `第 ${index + 1} 张生成失败`;
          const failedImage: StoredImage = {
            id: `${conversationId}-${index}`,
            status: "error",
            error: message,
          };

          await updateConversation(conversationId, (current) => ({
            ...(current ?? draftConversation),
            images: (current?.images ?? draftConversation.images).map((image) =>
              image.id === failedImage.id ? failedImage : image,
            ),
          }));

          throw error;
        }
      });

      const settled = await Promise.allSettled(tasks);
      const successCount = settled.filter((item): item is PromiseFulfilledResult<StoredImage> => item.status === "fulfilled")
        .length;
      const failedCount = settled.length - successCount;

      if (successCount === 0) {
        const firstError = settled.find((item) => item.status === "rejected");
        throw new Error(firstError?.status === "rejected" ? String(firstError.reason) : "生成图片失败");
      }

      await updateConversation(conversationId, (current) => ({
        ...(current ?? draftConversation),
        status: failedCount > 0 ? "error" : "success",
        error: failedCount > 0 ? `其中 ${failedCount} 张生成失败` : undefined,
      }));
      await loadQuota();

      if (failedCount > 0) {
        toast.error(`已完成 ${successCount} 张，另有 ${failedCount} 张未生成成功`);
      } else {
        toast.success(`已生成 ${successCount} 张图片`);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "生成图片失败";
      await persistConversation({
        ...draftConversation,
        status: "error",
        error: message,
      });
      toast.error(message);
    } finally {
      setIsGenerating(false);
    }
  };

  return (
    <>
      <section className="mx-auto grid h-[calc(100vh-5rem)] min-h-0 w-full max-w-[1380px] grid-cols-1 gap-3 px-3 pb-6 lg:grid-cols-[240px_minmax(0,1fr)]">
        <aside className="min-h-0 border-r border-stone-200/70 pr-3">
          <div className="flex h-full min-h-0 flex-col gap-3 py-2">
            <div className="flex items-center gap-2">
              <Button
                className="h-10 flex-1 rounded-xl bg-stone-950 text-white hover:bg-stone-800"
                onClick={handleCreateDraft}
              >
                <MessageSquarePlus className="size-4" />
                新建对话
              </Button>
              <Button
                variant="outline"
                className="h-10 rounded-xl border-stone-200 bg-white/85 px-3 text-stone-600 hover:bg-white"
                onClick={() => void handleClearHistory()}
                disabled={conversations.length === 0}
              >
                <Trash2 className="size-4" />
              </Button>
            </div>

            <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
              {isLoadingHistory ? (
                <div className="flex items-center gap-2 px-2 py-3 text-sm text-stone-500">
                  <LoaderCircle className="size-4 animate-spin" />
                  正在读取会话记录
                </div>
              ) : conversations.length === 0 ? (
                <div className="px-2 py-3 text-sm leading-6 text-stone-500">
                  还没有图片记录，输入提示词后会在这里显示。
                </div>
              ) : (
                conversations.map((conversation) => {
                  const active = conversation.id === selectedConversationId;
                  return (
                    <div
                      key={conversation.id}
                      className={cn(
                        "group relative w-full border-l-2 px-3 py-3 text-left transition",
                        active
                          ? "border-stone-900 bg-black/[0.03] text-stone-950"
                          : "border-transparent text-stone-700 hover:border-stone-300 hover:bg-white/40",
                      )}
                    >
                      <button
                        type="button"
                        onClick={() => setSelectedConversationId(conversation.id)}
                        className="block w-full pr-8 text-left"
                      >
                        <div className="truncate text-sm font-semibold">{conversation.title}</div>
                        <div className={cn("mt-1 text-xs", active ? "text-stone-500" : "text-stone-400")}>
                          {formatConversationTime(conversation.createdAt)}
                        </div>
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleDeleteConversation(conversation.id)}
                        className="absolute top-3 right-2 inline-flex size-7 items-center justify-center rounded-md text-stone-400 opacity-0 transition hover:bg-stone-100 hover:text-rose-500 group-hover:opacity-100"
                        aria-label="删除会话"
                      >
                        <Trash2 className="size-4" />
                      </button>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </aside>

        <div className="flex min-h-0 flex-col gap-4">
          <div
            ref={resultsViewportRef}
            className="hide-scrollbar min-h-0 flex-1 overflow-y-auto px-2 py-3 sm:px-4 sm:py-4"
          >
            {!selectedConversation ? (
              <div className="flex h-full min-h-[420px] items-center justify-center text-center">
                <div className="w-full max-w-4xl">
                  <h1
                    className="text-3xl font-semibold tracking-tight text-stone-950 md:text-5xl"
                    style={{
                      fontFamily: '"Palatino Linotype","Book Antiqua","URW Palladio L","Times New Roman",serif',
                    }}
                  >
                    Turn ideas into images

                  </h1>
                  <p
                    className="mt-4 text-[15px] italic tracking-[0.01em] text-stone-500"
                    style={{
                      fontFamily: '"Palatino Linotype","Book Antiqua","URW Palladio L","Times New Roman",serif',
                    }}
                  >
                    Describe a scene, a mood, or a character, and let the next image start here.
                  </p>
                </div>
              </div>
            ) : (
              <div className="mx-auto flex w-full max-w-[980px] flex-col gap-5">
                <div className="flex justify-end">
                  <div className="max-w-[80%] px-1 pt-1 text-right">
                    {selectedConversation.referenceImage && (
                      <div className="mb-2 flex flex-wrap justify-end gap-1.5">
                        {((selectedConversation as any).referenceImages || [selectedConversation.referenceImage]).map(
                          (img: string, i: number) => (
                            <img
                              key={i}
                              src={img}
                              alt={`参考图 ${i + 1}`}
                              className="h-20 w-20 rounded-xl border border-stone-200 object-cover"
                            />
                          ),
                        )}
                      </div>
                    )}
                    <div className="text-[15px] leading-8 text-stone-700">
                      {selectedConversation.prompt}
                    </div>
                  </div>
                </div>

                <div className="flex justify-start">
                  <div className="w-full p-1">
                    <div className="mb-4 flex flex-wrap items-center gap-2 text-xs text-stone-500">
                      <span className="rounded-full bg-stone-100 px-3 py-1">{selectedConversation.model}</span>
                      <span className="rounded-full bg-stone-100 px-3 py-1">{selectedConversation.count} 张</span>
                      <span className="rounded-full bg-stone-100 px-3 py-1">
                        {formatConversationTime(selectedConversation.createdAt)}
                      </span>
                    </div>

                    {selectedConversation.status === "error" && selectedConversation.images.length === 0 ? (
                      <div className="border-l-2 border-rose-300 bg-rose-50/70 px-4 py-4 text-sm leading-6 text-rose-600">
                        {selectedConversation.error || "生成失败"}
                      </div>
                    ) : null}

                    {selectedConversation.images.length > 0 ? (
                      <div className="columns-1 gap-4 space-y-4 sm:columns-2 xl:columns-3">
                        {selectedConversation.images.map((image, index) => (
                          <div key={image.id} className="break-inside-avoid overflow-hidden rounded-[22px]">
                            {image.status === "success" && image.b64_json ? (
                              <Image
                                src={`data:image/png;base64,${image.b64_json}`}
                                alt={`Generated result ${index + 1}`}
                                width={1024}
                                height={1024}
                                unoptimized
                                className="block h-auto w-full"
                              />
                            ) : image.status === "error" ? (
                              <div className="flex min-h-[320px] items-center justify-center bg-rose-50 px-6 py-8 text-center text-sm leading-6 text-rose-600">
                                {image.error || "生成失败"}
                              </div>
                            ) : (
                              <div className="flex min-h-[320px] flex-col items-center justify-center gap-3 bg-stone-100/80 px-6 py-8 text-center text-stone-500">
                                <div className="rounded-full bg-white p-3 shadow-sm">
                                  <LoaderCircle className="size-5 animate-spin" />
                                </div>
                                <p className="text-sm">正在生成图片...</p>
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    ) : null}

                    {selectedConversation.status === "error" && selectedConversation.images.length > 0 ? (
                      <div className="mt-4 border-l-2 border-amber-300 bg-amber-50/70 px-4 py-3 text-sm leading-6 text-amber-700">
                        {selectedConversation.error}
                      </div>
                    ) : null}
                  </div>
                </div>
              </div>
            )}
          </div>

          <div className="shrink-0 flex justify-center">
            <div
              className="overflow-hidden rounded-[32px] border border-stone-200/80 bg-white shadow-[0_18px_48px_rgba(28,25,23,0.08)]"
              style={{ width: "min(980px, 100%)" }}
            >
              <div
                className="relative cursor-text"
                onClick={() => {
                  textareaRef.current?.focus();
                }}
              >
                {/* Reference image preview */}
                {referencePreviews.length > 0 && (
                  <div className="flex flex-wrap items-center gap-2 px-6 pt-4 pb-0">
                    {referencePreviews.map((preview, index) => (
                      <div key={index} className="group relative inline-block">
                        <img
                          src={preview}
                          alt={`参考图 ${index + 1}`}
                          className="h-16 w-16 rounded-xl border border-stone-200 object-cover"
                        />
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            removeReferenceImage(index);
                          }}
                          className="absolute -top-1.5 -right-1.5 flex size-5 items-center justify-center rounded-full bg-stone-800 text-white opacity-0 transition group-hover:opacity-100"
                        >
                          <X className="size-3" />
                        </button>
                      </div>
                    ))}
                    <span className="text-xs text-stone-400">{referencePreviews.length} 张参考图</span>
                  </div>
                )}

                <Textarea
                  ref={textareaRef}
                  value={imagePrompt}
                  onChange={(event) => setImagePrompt(event.target.value)}
                  placeholder={referenceImages.length > 0 ? "描述你想要的修改..." : "输入你想要生成的画面"}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      if (!isGenerating) {
                        void handleGenerateImage();
                      }
                    }
                  }}
                  onPaste={(event) => {
                    const items = event.clipboardData?.items;
                    if (!items) return;
                    const imageFiles: File[] = [];
                    for (const item of Array.from(items)) {
                      if (item.type.startsWith("image/")) {
                        const file = item.getAsFile();
                        if (file) imageFiles.push(file);
                      }
                    }
                    if (imageFiles.length > 0) {
                      event.preventDefault();
                      handleFileSelect(new DataTransfer().files); // dummy, we handle manually
                      const newPreviews: string[] = [];
                      for (const file of imageFiles) {
                        if (file.size > 20 * 1024 * 1024) {
                          toast.error(`${file.name} 超过 20MB`);
                          continue;
                        }
                        newPreviews.push(URL.createObjectURL(file));
                      }
                      setReferenceImages((prev) => [...prev, ...imageFiles]);
                      setReferencePreviews((prev) => [...prev, ...newPreviews]);
                    }
                  }}
                  className={cn(
                    "resize-none rounded-[32px] border-0 bg-transparent px-6 pb-20 text-[15px] leading-7 text-stone-900 shadow-none placeholder:text-stone-400 focus-visible:ring-0",
                    referencePreviews.length > 0 ? "min-h-[100px] pt-3" : "min-h-[148px] pt-6",
                  )}
                />

                {/* Hidden file input */}
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/*"
                  multiple
                  className="hidden"
                  onChange={(e) => {
                    handleFileSelect(e.target.files);
                    if (fileInputRef.current) fileInputRef.current.value = "";
                  }}
                />

                <div className="absolute inset-x-0 bottom-0 flex items-center justify-between bg-gradient-to-t from-white via-white/95 to-transparent px-4 pb-4 pt-10 sm:px-6">
                  <div className="flex items-center gap-3">
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        fileInputRef.current?.click();
                      }}
                      className={cn(
                        "inline-flex size-10 items-center justify-center rounded-full border transition",
                        referenceImages.length > 0
                          ? "border-emerald-300 bg-emerald-50 text-emerald-600"
                          : "border-stone-200 bg-white text-stone-500 hover:bg-stone-50 hover:text-stone-700",
                      )}
                      title="上传参考图"
                    >
                      <ImagePlus className="size-4" />
                    </button>
                    <div className="rounded-full bg-stone-100 px-3 py-2 text-xs font-medium text-stone-600">
                      剩余额度 {availableQuota}
                    </div>
                    <Select value={imageModel} onValueChange={(value) => setImageModel(value as ImageModel)}>
                      <SelectTrigger className="h-10 w-[164px] rounded-full border-stone-200 bg-white text-sm font-medium text-stone-700 shadow-none focus-visible:ring-0">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {imageModelOptions.map((item) => (
                          <SelectItem key={item.value} value={item.value}>
                            {item.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>

                    <div className="flex items-center gap-2 rounded-full border border-stone-200 bg-white px-3 py-1">
                      <span className="text-sm font-medium text-stone-700">张数</span>
                      <Input
                        type="number"
                        min="1"
                        max="10"
                        step="1"
                        value={imageCount}
                        onChange={(event) => setImageCount(event.target.value)}
                        className="h-8 w-[64px] border-0 bg-transparent px-0 text-center text-sm font-medium text-stone-700 shadow-none focus-visible:ring-0"
                      />
                    </div>
                  </div>

                  <button
                    type="button"
                    onClick={() => void handleGenerateImage()}
                    disabled={isGenerating}
                    className="inline-flex size-11 shrink-0 items-center justify-center rounded-full bg-stone-950 text-white transition hover:bg-stone-800 disabled:cursor-not-allowed disabled:bg-stone-300"
                    aria-label="生成图片"
                  >
                    {isGenerating ? <LoaderCircle className="size-4 animate-spin" /> : <ArrowUp className="size-4" />}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
