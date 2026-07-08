import React from "react";

interface CustomerTypeOption {
  value: "individual" | "company";
  label: string;
  icon: React.ComponentType<{ size?: number; className?: string }>;
  desc: string;
}

interface CustomerTypeSelectorProps {
  customerType: "individual" | "company";
  onChange: (type: "individual" | "company") => void;
  availableTypes: CustomerTypeOption[];
}

export const CustomerTypeSelector: React.FC<CustomerTypeSelectorProps> = ({
  customerType,
  onChange,
  availableTypes,
}) => {
  return (
    <div className="mb-3 sm:mb-6">
      <div className="grid grid-cols-2 gap-2 sm:gap-4">
        {availableTypes.map((type) => {
          const IconComponent = type.icon;
          return (
            <label
              key={type.value}
              className={`relative flex items-center gap-2 p-2.5 sm:p-4 border-2 rounded-lg cursor-pointer transition-all hover:bg-gray-50 dark:hover:bg-gray-700 ${
                customerType === type.value
                  ? "border-beveren-500 bg-beveren-50 dark:bg-beveren-900/20"
                  : "border-gray-200 dark:border-gray-600"
              }`}
            >
              <input
                type="radio"
                name="customerType"
                value={type.value}
                checked={customerType === type.value}
                onChange={() => onChange(type.value)}
                className="sr-only"
              />
              <IconComponent
                size={18}
                className={`shrink-0 sm:size-6 ${
                  customerType === type.value
                    ? "text-beveren-600"
                    : "text-gray-400"
                }`}
              />
              <div className="flex flex-col items-start min-w-0">
                <span
                  className={`font-medium text-xs sm:text-sm ${
                    customerType === type.value
                      ? "text-beveren-900 dark:text-beveren-100"
                      : "text-gray-900 dark:text-white"
                  }`}
                >
                  {type.label}
                </span>
                <span
                  className={`hidden sm:block text-xs ${
                    customerType === type.value
                      ? "text-beveren-700 dark:text-beveren-300"
                      : "text-gray-500 dark:text-gray-400"
                  }`}
                >
                  {type.desc}
                </span>
              </div>
            </label>
          );
        })}
      </div>
    </div>
  );
};