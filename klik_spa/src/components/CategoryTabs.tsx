"use client";

import { useProductStore } from "../stores/productStore";

interface CategoryTabsProps {
  selectedCategory: string;
  onCategoryChange: (category: string) => void;
  isMobile?: boolean;
}

export default function CategoryTabs({
  selectedCategory,
  onCategoryChange,
  isMobile = false,
}: CategoryTabsProps) {
  const itemGroups = useProductStore((state) => state.itemGroups);
  const isLoading = useProductStore((state) => state.isLoading);
  const isSearching = useProductStore((state) => state.isSearching);
  const searchProducts = useProductStore((state) => state.searchProducts);
  const searchQuery = useProductStore((state) => state.searchQuery);

  const isValidating = isLoading && !isSearching;

  if (isValidating) {
    return (
      <div className="flex space-x-1.5 overflow-x-auto py-1.5 scrollbar-hide">
        {[1, 2, 3, 4, 5].map((i) => (
          <div
            key={i}
            className="h-8 rounded-lg bg-gray-200 dark:bg-gray-700 animate-pulse min-w-[80px]"
          />
        ))}
      </div>
    );
  }

  const allItemsCount = itemGroups.reduce((sum, group) => sum + (group.total_count || group.count || 0), 0);

  const categories = [
    {
      id: "all",
      name: "All Items",
      count: allItemsCount,
    },
    ...itemGroups.map((group) => ({
      id: group.id,
      name: group.name,
      count: group.total_count || group.count || 0,
    })),
  ];

  const handleCategoryClick = (categoryId: string) => {
    onCategoryChange(categoryId);
    if (searchQuery) {
      searchProducts(searchQuery);
    }
  };

  if (categories.length === 1 && categories[0].id === "all" && categories[0].count === 0) {
    return (
      <div className="text-center py-4 text-gray-500 dark:text-gray-400">
        No categories available
      </div>
    );
  }

  return (
    <div className="flex space-x-1.5 overflow-x-auto py-1.5 scrollbar-hide">
      {categories.map((category) => {
        const isActive = selectedCategory === category.id;
        return (
          <button
            key={category.id}
            onClick={() => handleCategoryClick(category.id)}
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg whitespace-nowrap transition-all duration-150 flex-shrink-0 ${
              isActive
                ? "bg-beveren-600 dark:bg-beveren-500 text-white shadow-sm"
                : "bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700 border border-gray-200 dark:border-gray-700"
            }`}
          >
            <span className={`font-medium ${isMobile ? "text-xs" : "text-sm"}`}>
              {category.name}
            </span>
            <span
              className={`text-[10px] font-semibold px-1.5 py-0.5 rounded-md leading-none ${
                isActive
                  ? "bg-white/20 text-white"
                  : "bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400"
              }`}
            >
              {category.count}
            </span>
          </button>
        );
      })}
    </div>
  );
}