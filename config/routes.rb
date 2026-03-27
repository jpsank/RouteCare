Rails.application.routes.draw do
  devise_for :users
  root "home#index"

  namespace :api do
    namespace :v1 do
      resources :patients do
        member do
          post :deactivate
        end
      end
      resources :calendar_blocks, only: %i[index create update destroy]
      resources :alerts, only: %i[index update]
      resources :messages, only: %i[index create] do
        collection do
          post :create_inbound, path: "inbound"
        end
      end

      resource :schedule, only: %i[show create] do
        post :optimize
        post :approve
      end

      resources :visits, only: %i[index update] do
        member do
          post :reschedule
        end
      end
    end
  end
  # Define your application routes per the DSL in https://guides.rubyonrails.org/routing.html

  # Reveal health status on /up that returns 200 if the app boots with no exceptions, otherwise 500.
  # Can be used by load balancers and uptime monitors to verify that the app is live.
  get "up" => "rails/health#show", as: :rails_health_check

  # Render dynamic PWA files from app/views/pwa/* (remember to link manifest in application.html.erb)
  # get "manifest" => "rails/pwa#manifest", as: :pwa_manifest
  # get "service-worker" => "rails/pwa#service_worker", as: :pwa_service_worker

end
